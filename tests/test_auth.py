from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import auth
from app.accounts import User
from app.database import get_session

ACCOUNT = {"telegram_id": 2**34, "password": "пароль123"}
KEY = {"X-Bot-Key": "test-bot-key"}


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_SERVICE_KEY", KEY["X-Bot-Key"])
    engine = create_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    User.__table__.create(engine)
    app = FastAPI()
    app.include_router(auth.router)

    def get_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_session] = get_db
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_register_requires_bot_and_unique_telegram_id(service):
    client, engine = service
    assert client.post("/auth/register", json=ACCOUNT).status_code == 403
    invalid = {**ACCOUNT, "password": "123"}
    assert client.post("/auth/register", json=invalid, headers=KEY).status_code == 422
    response = client.post("/auth/register", json=ACCOUNT, headers=KEY)
    assert response.status_code == 201
    token = response.json()["access_token"]
    assert client.post("/auth/register", json=ACCOUNT, headers=KEY).status_code == 409
    with Session(engine) as db:
        user = db.scalar(select(User))
        assert user.telegram_id == ACCOUNT["telegram_id"]
        assert ACCOUNT["password"] not in user.password_hash
        assert token != user.token_digest


def test_login_rotates_tokens_logout_revokes_and_expiry_is_enforced(
    service, monkeypatch
):
    client, _ = service
    token = client.post("/auth/register", json=ACCOUNT, headers=KEY).json()[
        "access_token"
    ]
    assert client.post("/auth/login", json=ACCOUNT).status_code == 403
    response = client.post("/auth/login", json=ACCOUNT, headers=KEY)
    assert response.status_code == 200
    latest = response.json()["access_token"]

    def logout(raw):
        return client.post(
            "/auth/logout", headers={"Authorization": f"Bearer {raw}"}
        ).status_code

    assert latest != token
    assert logout(token) == 401
    assert logout(latest) == 204
    assert logout(latest) == 401
    latest = client.post("/auth/login", json=ACCOUNT, headers=KEY).json()[
        "access_token"
    ]
    future = int(auth.time.time()) + auth.TOKEN_SECONDS + 1
    monkeypatch.setattr(auth.time, "time", lambda: future)
    assert logout(latest) == 401


def test_simultaneous_attempts_lock_and_unlock(service, monkeypatch):
    client, engine = service
    client.post("/auth/register", json=ACCOUNT, headers=KEY)
    wrong = {**ACCOUNT, "password": "wrongpass"}

    def fail(_):
        return client.post("/auth/login", json=wrong, headers=KEY).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(fail, range(auth.MAX_FAILURES + 2)))
    assert codes.count(401) == auth.MAX_FAILURES
    assert codes.count(429) == 2
    assert client.post("/auth/login", json=ACCOUNT, headers=KEY).status_code == 429
    with Session(engine) as db:
        deadline = db.scalar(select(User.locked_until))
    monkeypatch.setattr(auth.time, "time", lambda: deadline)
    assert client.post("/auth/login", json=ACCOUNT, headers=KEY).status_code == 200
