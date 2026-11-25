import json
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from app.bot_presenter import HabitPresenter, Input


def message(text, *, kind="private"):
    return SimpleNamespace(
        chat=SimpleNamespace(type=kind, id=19),
        from_user=SimpleNamespace(id=19),
        message_id=2,
        text=text,
    )


def setup(handler):
    bot = Mock()
    client = httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))
    return HabitPresenter(bot, client, "service-key"), bot


@pytest.mark.parametrize("action", ["register", "login"])
def test_password_flow(action):
    def handle(request):
        bot.delete_message.assert_called_once_with(19, 2)
        assert request.url.path == f"/auth/{action}"
        assert request.headers["X-Bot-Key"] == "service-key"
        assert json.loads(request.content) == {
            "telegram_id": 19,
            "password": "private-password",
        }
        return httpx.Response(200, json={"access_token": "secret-bearer"})

    tracker, bot = setup(handle)
    tracker.handle_action(19, action)
    assert "Telegram" in bot.send_message.call_args.args[1]
    tracker.handle_message(message("private-password", kind="group"))
    bot.delete_message.assert_not_called()
    tracker.handle_message(message("private-password"))
    assert tracker.get_token(19) == "secret-bearer"
    assert "private-password" not in str(bot.send_message.call_args_list)
    assert "secret-bearer" not in str(bot.send_message.call_args_list)


def test_password_expired_is_deleted_before_discarding():
    tracker, bot = setup(lambda _: pytest.fail("unexpected API request"))
    tracker.inputs[19] = Input("login", monotonic() - 1)
    tracker.handle_message(message("expired-password"))
    bot.delete_message.assert_called_once_with(19, 2)
    assert 19 not in tracker.inputs
    assert "expired-password" not in str(bot.send_message.call_args_list)


@pytest.mark.parametrize("mark,expected", [("0", False), ("1", True)])
def test_reminder_buttons_and_session_loss(mark, expected):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            assert request.headers["Authorization"] == "Bearer token"
            assert request.method == "PUT"
            assert request.url.path == "/habits/42/marks/2026-11-25"
            assert json.loads(request.content) == {"completed": expected}
            return httpx.Response(200, json={})
        return httpx.Response(401)

    tracker, bot = setup(handle)
    tracker.sessions[19] = ("token", monotonic() + 20)
    tracker.handle_action(19, f"mark:42:2026-11-25:{mark}")
    tracker.handle_action(19, "today")
    assert not tracker.get_token(19)
    tracker.handle_action(19, "today")
    assert len(requests) == 2
    assert "Войдите" in bot.send_message.call_args.args[1]


def test_edit_target_and_logout():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(204)

    tracker, _bot = setup(handle)
    tracker.sessions[19] = ("token", monotonic() + 60)
    tracker.handle_action(19, "edit:28")
    tracker.handle_message(message("  Прогулка  "))
    tracker.handle_message(message("0"))
    assert not requests
    tracker.handle_message(message("20"))
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/habits/28"
    assert json.loads(requests[0].content) == {"name": "Прогулка", "target": 20}
    tracker.handle_action(19, "logout")
    assert requests[1].url.path == "/auth/logout"
    assert not tracker.get_token(19)


def test_default_goal_and_archive_are_accessible_from_menu():
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={})
        return httpx.Response(
            200,
            json={
                "date": "2026-11-25",
                "habits": [
                    {
                        "id": 28,
                        "name": "Прогулка",
                        "target": 21,
                        "count": 21,
                        "completed": None,
                    }
                ],
            },
        )

    tracker, bot = setup(handle)
    tracker.sessions[19] = ("token", monotonic() + 60)
    tracker.handle_action(19, "add")
    tracker.handle_message(message("Прогулка"))
    button = bot.send_message.call_args.kwargs["reply_markup"].keyboard[0][0]
    tracker.handle_action(19, button.callback_data)
    tracker.handle_action(19, button.callback_data)
    assert len(requests) == 1
    assert json.loads(requests[0].content) == {"name": "Прогулка", "target": 21}
    tracker.handle_action(19, "all")
    assert requests[-1].url.params["include_completed"] == "true"
    card = bot.send_message.call_args_list[-2]
    actions = [
        button.callback_data
        for row in card.kwargs["reply_markup"].keyboard
        for button in row
    ]
    assert actions == ["edit:28", "delete:28"]


def test_settings_and_confirmed_deletion():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/settings":
            return httpx.Response(
                200, json={"timezone": "UTC", "reminder_time": "20:00"}
            )
        return httpx.Response(204)

    tracker, bot = setup(handle)
    tracker.sessions[19] = ("token", monotonic() + 60)
    tracker.handle_action(19, "settings")
    assert "20:00" in bot.send_message.call_args.args[1]
    for field, value in [("timezone", "Asia/Kamchatka"), ("reminder_time", "08:30")]:
        tracker.handle_action(19, field)
        tracker.handle_message(message(value))
        assert requests[-1].method == "PATCH"
        assert json.loads(requests[-1].content) == {field: value}
    tracker.handle_action(19, "delete:28")
    assert len(requests) == 3
    tracker.handle_action(19, "confirm:28")
    assert requests[-1].method == "DELETE"
    assert requests[-1].url.path == "/habits/28"


def test_private_polling_entrypoint(monkeypatch):
    from app import bot as entrypoint

    telegram = Mock()
    build_bot = Mock(return_value=telegram)
    monkeypatch.setattr(entrypoint.telebot, "TeleBot", build_bot)
    monkeypatch.setattr(entrypoint.telebot.logger, "disabled", False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("BOT_SERVICE_KEY", "service")
    monkeypatch.setenv("API_URL", "http://api")
    entrypoint.run_bot()
    build_bot.assert_called_once_with("dummy", threaded=False)
    telegram.register_callback_query_handler.assert_called_once()
    telegram.register_message_handler.assert_called_once()
    telegram.infinity_polling.assert_called_once_with(
        skip_pending=True, allowed_updates=["message", "callback_query"]
    )
