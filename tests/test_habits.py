from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Header
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import habits
from app.accounts import User
from app.auth import get_current_user
from app.database import Base, get_session
from app.habits import service


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'habits.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [User(id=i, telegram_id=i, password_hash="test") for i in (1, 2)]
        )
        session.commit()

    def database():
        with Session(engine) as session:
            yield session

    def user(
        session: Annotated[Session, Depends(get_session)],
        owner: Annotated[int, Header()] = 1,
    ):
        return session.get(User, owner)

    app = FastAPI()
    app.include_router(habits.router)
    app.dependency_overrides[get_session] = database
    app.dependency_overrides[get_current_user] = user
    with TestClient(app) as client:
        yield client
    engine.dispose()


def create(client, target=2):
    response = client.post("/habits", json={"name": "  Зарядка ", "target": target})
    assert response.status_code == 201
    habit = response.json()
    assert habit["name"] == "Зарядка"
    assert habit["count"] == 0
    assert habit["completed"] is None
    return habit["id"]


def test_marks_rollover_target_and_reopening(client, monkeypatch):
    day = date(2026, 1, 1)
    monkeypatch.setattr(service, "get_local_day", lambda user: day)
    habit_id = create(client)
    path = f"/habits/{habit_id}"

    def mark(value):
        response = client.put(f"{path}/marks/{day}", json={"completed": value})
        assert response.status_code == 200
        return response.json()

    assert mark(True)["count"] == 1
    assert mark(True)["count"] == 1
    assert mark(False)["count"] == 0
    assert mark(True)["count"] == 1
    day = date(2026, 1, 2)
    assert client.get("/habits").json()["habits"][0]["completed"] is None
    assert mark(True)["count"] == 2
    assert client.get("/habits").json()["habits"][0]["completed"] is True
    day = date(2026, 1, 3)
    assert client.get("/habits").json() == {"date": str(day), "habits": []}
    archived = client.get("/habits?include_completed=true").json()["habits"]
    assert [(row["id"], row["count"], row["completed"]) for row in archived] == [
        (habit_id, 2, None)
    ]
    for invalid in ("2026-01-02", "2026-01-03", "2026-01-04"):
        assert (
            client.put(f"{path}/marks/{invalid}", json={"completed": True}).status_code
            == 409
        )
    assert client.patch(path, json={"target": 3}).json()["count"] == 2
    assert client.get("/habits").json()["habits"][0]["id"] == habit_id
    assert mark(False)["completed"] is False
    assert client.delete(path).status_code == 204
    assert client.get("/habits").json()["habits"] == []


def test_concurrent_marks_are_idempotent(client):
    habit_id = create(client, 1)
    day = client.get("/habits").json()["date"]

    def mark(_):
        response = client.put(
            f"/habits/{habit_id}/marks/{day}", json={"completed": True}
        )
        return response.status_code, response.json().get("count")

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(mark, range(8))) == [(200, 1)] * 8


def test_habits_are_private_and_validate_input(client):
    path = f"/habits/{create(client)}"
    other = {"owner": "2"}
    today = client.get("/habits").json()["date"]
    assert client.get("/habits", headers=other).json()["habits"] == []
    assert client.patch(path, headers=other, json={"name": "Чужая"}).status_code == 404
    assert client.delete(path, headers=other).status_code == 404
    assert (
        client.put(
            f"{path}/marks/{today}", headers=other, json={"completed": False}
        ).status_code
        == 404
    )
    for data in ({"name": "   "}, {"name": None}, {"target": 0}, {"target": True}, {}):
        assert client.patch(path, json=data).status_code == 422
    assert (
        client.put(f"{path}/marks/{today}", json={"completed": "да"}).status_code == 422
    )


def test_settings_and_local_midnight(client, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 21, 30, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(service, "datetime", FixedDatetime)
    assert client.get("/habits").json()["date"] == "2026-01-01"
    settings = {"timezone": "Europe/Moscow", "reminder_time": "00:05"}
    assert client.patch("/settings", json=settings).json() == settings
    assert client.get("/settings").json() == settings
    assert client.get("/habits").json()["date"] == "2026-01-02"
    for invalid in (
        {"timezone": "Europe/Fake"},
        {"timezone": "../etc"},
        {"reminder_time": "24:00"},
    ):
        assert client.patch("/settings", json=invalid).status_code == 422
    assert client.get("/settings", headers={"owner": "2"}).json() != settings


def test_create_habit_with_default_target(client):
    response = client.post("/habits", json={"name": "Прогулка"})
    assert response.status_code == 201
    assert response.json()["target"] == 21
    assert client.get("/habits").json()["habits"][0]["target"] == 21
