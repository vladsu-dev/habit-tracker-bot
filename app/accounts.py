from datetime import time

from sqlalchemy import BigInteger, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    reminder_time: Mapped[time] = mapped_column(Time, default=time(20))
    password_hash: Mapped[str] = mapped_column(String(128))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[int | None] = mapped_column(BigInteger)
    token_digest: Mapped[str | None] = mapped_column(String(64), unique=True)
    token_expires_at: Mapped[int | None] = mapped_column(BigInteger)
