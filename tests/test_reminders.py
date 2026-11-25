from datetime import UTC, datetime, time
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import reminders
from app.accounts import User
from app.database import Base
from app.habits.models import Habit


@pytest.fixture
def create_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield sessionmaker(engine)
    engine.dispose()


def create_recipient(session, user_id, timezone):
    session.add(
        User(
            id=user_id,
            telegram_id=100 + user_id,
            password_hash="test",
            timezone=timezone,
            reminder_time=time(0),
        )
    )
    session.add(Habit(user_id=user_id, name="Чтение", target=2))
    session.commit()


def test_worker_uses_one_local_date_for_scheduling_and_habits(create_session):
    gateway = Mock()
    with create_session() as session:
        create_recipient(session, 1, "Asia/Kamchatka")
        # UTC evening falls on the following local calendar date
        scheduled = datetime(2025, 7, 3, 18, 0, tzinfo=UTC)
        reminders.send_daily_reminder(session, 1, gateway, scheduled)
        reminders.send_daily_reminder(session, 1, gateway, scheduled)
    gateway.send_message.assert_called_once()
    assert gateway.send_message.call_args.args == (101, "Пора отметить привычки.")


def test_one_invalid_timezone_does_not_stop_delivery_to_others(
    create_session, monkeypatch
):
    with create_session() as session:
        create_recipient(session, 1, "Unknown/OldZone")
        create_recipient(session, 2, "UTC")
    gateway = Mock()
    monkeypatch.setattr(reminders, "SessionFactory", create_session)
    reminders.run_delivery_cycle(gateway)
    gateway.send_message.assert_called_once()
    assert gateway.send_message.call_args.args[0] == 102
