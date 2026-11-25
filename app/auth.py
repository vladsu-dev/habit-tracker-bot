import hashlib
import os
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.accounts import User
from app.database import get_session

router = APIRouter(prefix="/auth", tags=["Авторизация"])
DatabaseSession = Annotated[Session, Depends(get_session)]
Bearer = Annotated[
    HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
]
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
TOKEN_SECONDS = 7 * 24 * 3600


class Credentials(BaseModel):
    telegram_id: int = Field(gt=0, le=2**63 - 1)
    password: SecretStr


class AccessToken(BaseModel):
    access_token: str


def require_bot_key(x_bot_key: Annotated[str | None, Header()] = None) -> None:
    expected = os.getenv("BOT_SERVICE_KEY")
    if not expected:
        raise HTTPException(503, "Ключ бота не настроен")
    if not secrets.compare_digest((x_bot_key or "").encode(), expected.encode()):
        raise HTTPException(403, "Неверный ключ бота")


def encode_password(credentials: Credentials) -> bytes:
    value = credentials.password.get_secret_value()
    if not 8 <= len(value) <= 128:
        raise HTTPException(422, "Введите пароль длиной от 8 до 128 символов")
    return value.encode()


def derive_password(password: bytes, salt: bytes) -> str:
    return hashlib.scrypt(
        password, salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024**2, dklen=32
    ).hex()


def issue_token(user: User) -> AccessToken:
    raw = secrets.token_urlsafe(32)
    user.token_digest = hashlib.sha256(raw.encode()).hexdigest()
    user.token_expires_at = int(time.time()) + TOKEN_SECONDS
    return AccessToken(access_token=raw)


@router.post("/register", dependencies=[Depends(require_bot_key)], status_code=201)
def register(credentials: Credentials, session: DatabaseSession) -> AccessToken:
    password = encode_password(credentials)
    salt = secrets.token_bytes(16)
    user = User(
        telegram_id=credentials.telegram_id,
        password_hash=f"{salt.hex()}:{derive_password(password, salt)}",
    )
    token = issue_token(user)
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Этот аккаунт уже зарегистрирован") from None
    return token


@router.post("/login", dependencies=[Depends(require_bot_key)])
def login(credentials: Credentials, session: DatabaseSession) -> AccessToken:
    password = encode_password(credentials)
    # Первая запись сериализует попытки входа до проверки пароля и фиксации
    session.execute(
        update(User)
        .where(User.telegram_id == credentials.telegram_id)
        .values(failed_attempts=User.failed_attempts)
    )
    user = session.scalar(
        select(User).where(User.telegram_id == credentials.telegram_id)
    )
    now = int(time.time())
    if user is None:
        raise HTTPException(401, "Неверный аккаунт или пароль")
    if user.locked_until is not None:
        if user.locked_until > now:
            raise HTTPException(429, "Слишком много попыток. Повторите вход позже")
        user.locked_until = None
        user.failed_attempts = 0

    salt, expected = user.password_hash.split(":")
    actual = derive_password(password, bytes.fromhex(salt))
    if not secrets.compare_digest(actual, expected):
        user.failed_attempts += 1
        if user.failed_attempts >= MAX_FAILURES:
            user.locked_until = now + LOCK_SECONDS
        session.commit()
        raise HTTPException(401, "Неверный аккаунт или пароль")

    user.failed_attempts = 0
    token = issue_token(user)
    session.commit()
    return token


def get_current_user(credentials: Bearer, session: DatabaseSession) -> User:
    if credentials:
        digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        user = session.scalar(
            select(User).where(
                User.token_digest == digest, User.token_expires_at > int(time.time())
            )
        )
        if user is not None:
            return user
    raise HTTPException(
        401, "Войдите в аккаунт", headers={"WWW-Authenticate": "Bearer"}
    )


@router.post("/logout", status_code=204)
def logout(
    user: Annotated[User, Depends(get_current_user)], session: DatabaseSession
) -> Response:
    session.execute(
        update(User)
        .where(User.id == user.id, User.token_digest == user.token_digest)
        .values(token_digest=None, token_expires_at=None)
    )
    session.commit()
    return Response(status_code=204)
