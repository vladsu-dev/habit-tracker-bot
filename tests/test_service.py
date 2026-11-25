from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time
from secrets import randbelow
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.accounts import User
from app.database import Base, engine, get_session
from app.main import app
from app.reminders import send_daily_reminder


@pytest.fixture
def storage():
    if engine.dialect.name == "postgresql":
        yield engine
        return
    isolated = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(isolated)
    yield isolated
    isolated.dispose()


def test_authenticated_habits_and_reminders(storage, monkeypatch):
    def open_session():
        with Session(storage) as session:
            yield session

    app.dependency_overrides[get_session] = open_session
    service_key = "local-test-key"
    monkeypatch.setenv("BOT_SERVICE_KEY", service_key)
    identity = 10**12 + randbelow(10**12)
    credentials = {"telegram_id": identity, "password": "учебный-пароль"}
    gateway = Mock()
    client = TestClient(app)
    try:
        account = client.post(
            "/auth/register", json=credentials, headers={"X-Bot-Key": service_key}
        )
        assert account.status_code == 201
        headers = {"Authorization": "Bearer " + account.json()["access_token"]}
        habit = client.post(
            "/habits", json={"name": "Прогулка", "target": 2}, headers=headers
        )
        assert habit.status_code == 201
        assert client.patch(
            "/settings", json={"reminder_time": "00:00"}, headers=headers
        ).is_success
        now = datetime.now(UTC)
        with Session(storage) as session:
            from sqlalchemy import select

            user = session.scalar(select(User).where(User.telegram_id == identity))
            user_id = user.id
            day = now.astimezone(ZoneInfo(user.timezone)).date()
            assert user.reminder_time == time(0)
            send_daily_reminder(session, user_id, gateway, now)
        with Session(storage) as session:
            send_daily_reminder(session, user_id, gateway, now)
        gateway.send_message.assert_called_once()
        assert "Прогулка" in gateway.send_message.call_args.args[1]
        callback = (
            gateway.send_message.call_args.kwargs["reply_markup"]
            .keyboard[0][0]
            .callback_data
        )
        assert callback == f"mark:{habit.json()['id']}:{day}:1"
        path = f"/habits/{habit.json()['id']}/marks/{day}"
        if storage.dialect.name == "postgresql":

            def perform_mark(_):
                return client.put(
                    path, headers=headers, json={"completed": True}
                ).json()["count"]

            with ThreadPoolExecutor(max_workers=4) as pool:
                assert list(pool.map(perform_mark, range(8))) == [1] * 8
        assert (
            client.put(path, json={"completed": True}, headers=headers).json()["count"]
            == 1
        )
        assert client.post("/auth/logout", headers=headers).status_code == 204
        assert client.get("/habits", headers=headers).status_code == 401
    finally:
        with Session(storage) as session:
            session.execute(delete(User).where(User.telegram_id == identity))
            session.commit()
        app.dependency_overrides.pop(get_session)
        client.close()
