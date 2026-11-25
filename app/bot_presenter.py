"""Обработка пользовательских действий в Telegram."""

from dataclasses import dataclass
from datetime import date
from functools import partial
from secrets import token_hex
from time import monotonic

import httpx
import telebot
from requests.exceptions import RequestException

from app.bot_view import (
    PASSWORD_HINT,
    build_keyboard,
    build_login_menu,
    build_main_menu,
    render_habit_list,
)

SESSION_TTL = 7 * 24 * 60 * 60
INPUT_TTL = 10 * 60


@dataclass
class Input:
    action: str
    expires: float
    habit_id: str = ""
    name: str = ""
    prompt_key: str = ""


class HabitPresenter:
    def __init__(self, bot, client, service_key):
        self.bot = bot
        self.client = client
        self.service_key = service_key
        self.sessions = {}
        self.inputs = {}

    def send(self, user, text, markup=None):
        self.bot.send_message(user, text, reply_markup=markup)

    def get_token(self, user):
        session = self.sessions.get(user)
        if session and session[1] > monotonic():
            return session[0]
        if session:
            self.sessions.pop(user, None)
            self.inputs.pop(user, None)
        return None

    def prompt(self, user, action, text, habit_id="", name=""):
        pending = Input(action, monotonic() + INPUT_TTL, habit_id, name)
        markup = None
        if action == "delete":
            pending.prompt_key = token_hex(8)
            markup = build_keyboard(
                [("Удалить", f"confirm:{pending.prompt_key}"), ("Отмена", "menu")]
            )
        elif action == "target" and not habit_id:
            pending.prompt_key = token_hex(4)
            markup = build_keyboard(
                [("Выбрать 21 раз", f"default:{pending.prompt_key}")]
            )
        self.inputs[user] = pending
        self.send(user, text, markup)

    def request_api(self, user, method, path, payload=None, *, auth=False):
        if auth:
            headers = {"X-Bot-Key": self.service_key}
        else:
            token = self.get_token(user)
            if not token:
                self.send(
                    user,
                    "Войдите, чтобы открыть свои привычки.",
                    build_login_menu(),
                )
                return None
            headers = {"Authorization": f"Bearer {token}"}
        try:
            response = self.client.request(method, path, headers=headers, json=payload)
        except httpx.RequestError:
            self.send(user, "Нет связи с сервером. Попробуйте позже.")
            return None
        if response.is_success:
            return response.json() if response.content else {}
        if response.status_code == 401 and not auth:
            self.sessions.pop(user, None)
            self.inputs.pop(user, None)
            self.send(user, "Сеанс завершён. Войдите снова.", build_login_menu())
        elif auth:
            message = {
                401: "Войти не удалось. Проверьте пароль.",
                409: "Учётная запись уже существует.",
                422: "Проверьте длину пароля.",
                429: "Слишком много попыток. Повторите позже.",
            }.get(response.status_code, "Сервер не смог выполнить запрос.")
            self.send(user, message, build_login_menu())
        else:
            message = {
                400: "Сервер отклонил значение. Проверьте введённые данные.",
                404: "Привычка больше недоступна.",
                409: "Изменение недоступно. Обновите список привычек.",
                422: "Сервер отклонил значение. Проверьте введённые данные.",
            }.get(
                response.status_code,
                "Не получилось выполнить запрос. Попробуйте позже.",
            )
            self.send(user, message, build_main_menu())
        return None

    def show_habits(self, user, page=0, include_completed=False):
        path = "/habits?include_completed=true" if include_completed else "/habits"
        result = self.request_api(user, "GET", path)
        if result is None:
            return
        render_habit_list(partial(self.send, user), result, page, include_completed)

    def handle_action(self, user, data):
        if data.startswith(("default:", "confirm:")):
            button, prompt_key = data.split(":", 1)
            pending = self.inputs.get(user)
            if (
                not self.get_token(user)
                or pending is None
                or pending.expires <= monotonic()
                or not prompt_key
                or pending.prompt_key != prompt_key
                or pending.action != {"default": "target", "confirm": "delete"}[button]
            ):
                self.send(
                    user,
                    "Эта кнопка устарела.",
                    build_main_menu() if self.get_token(user) else build_login_menu(),
                )
                return
            if button == "default":
                self.save_habit(user, pending, 21)
            elif (
                self.request_api(user, "DELETE", f"/habits/{pending.habit_id}")
                is not None
            ):
                self.inputs.pop(user, None)
                self.send(user, "Привычка удалена.", build_main_menu())
            return
        self.inputs.pop(user, None)
        if data in {"register", "login"} and not self.get_token(user):
            self.prompt(user, data, PASSWORD_HINT)
            return
        if not self.get_token(user):
            self.send(
                user,
                "Войдите или создайте учётную запись.",
                build_login_menu(),
            )
            return
        parts = data.split(":")
        if data == "logout":
            result = self.request_api(user, "POST", "/auth/logout")
            if result is not None:
                self.sessions.pop(user, None)
                self.send(user, "Вы вышли из учётной записи.", build_login_menu())
        elif data in {"today", "all"}:
            self.show_habits(user, include_completed=data == "all")
        elif (
            len(parts) == 2
            and parts[0] in {"page", "allpage"}
            and parts[1].isascii()
            and parts[1].isdigit()
        ):
            self.show_habits(
                user, int(parts[1]), include_completed=parts[0] == "allpage"
            )
        elif data == "add":
            self.prompt(user, "name", "Введите название привычки (до 120 символов).")
        elif (
            len(parts) == 2
            and parts[0] in {"edit", "delete"}
            and is_valid_habit_id(parts[1])
        ):
            habit_id = parts[1]
            if parts[0] == "edit":
                self.prompt(user, "name", "Введите новое название привычки.", habit_id)
            else:
                self.prompt(user, "delete", "Удалить привычку и её отметки?", habit_id)
        elif (
            len(parts) == 4
            and parts[0] == "mark"
            and is_valid_habit_id(parts[1])
            and parts[3] in {"0", "1"}
        ):
            try:
                day = date.fromisoformat(parts[2]).isoformat()
            except ValueError:
                self.send(user, "Дата недоступна.", build_main_menu())
                return
            done = parts[3] == "1"
            if (
                self.request_api(
                    user, "PUT", f"/habits/{parts[1]}/marks/{day}", {"completed": done}
                )
                is not None
            ):
                self.send(
                    user,
                    f"{day} · {'Выполнено' if done else 'Пропущено'}",
                    build_main_menu(),
                )
        elif data == "settings":
            settings = self.request_api(user, "GET", "/settings")
            if settings is not None:
                self.send(
                    user,
                    f"Часовой пояс: {settings['timezone']}\nВремя напоминания: {settings['reminder_time']}",
                    build_keyboard(
                        [("Часовой пояс", "timezone"), ("Время", "reminder_time")],
                        [("Меню", "menu")],
                    ),
                )
        elif data == "timezone":
            self.prompt(
                user,
                data,
                "Введите часовой пояс, например Europe/Moscow или Asia/Kamchatka.",
            )
        elif data == "reminder_time":
            self.prompt(
                user, data, "Введите местное время в формате ЧЧ:ММ, например 20:00."
            )
        else:
            self.send(user, "Меню трекера привычек", build_main_menu())

    def handle_message(self, message):
        if (
            message.chat.type != "private"
            or message.from_user is None
            or message.chat.id != message.from_user.id
        ):
            return
        user = message.from_user.id
        pending = self.inputs.get(user)
        secret = pending and pending.action in {"register", "login"}
        if secret:
            try:
                self.bot.delete_message(user, message.message_id)
            except (telebot.apihelper.ApiException, RequestException):
                # Telegram may refuse deletion. Never echo the message or error details.
                self.send(
                    user,
                    "Автоудаление не удалось. Удалите сообщение с паролем вручную.",
                )
        value = message.text or ""
        if value == "/cancel":
            self.handle_action(user, "menu")
            return
        commands = {
            "/start": "menu",
            "/menu": "menu",
            "/today": "today",
            "/all": "all",
            "/add": "add",
            "/settings": "settings",
            "/logout": "logout",
            "/login": "login",
            "/register": "register",
        }
        if not secret and value in commands:
            self.handle_action(user, commands[value])
            return
        if pending and pending.expires <= monotonic():
            self.inputs.pop(user, None)
            self.send(
                user,
                "Время ввода истекло.",
                build_main_menu() if self.get_token(user) else build_login_menu(),
            )
            return
        if secret:
            if not 8 <= len(value) <= 128:
                self.send(
                    user,
                    "Нужно от 8 до 128 символов. Попробуйте ещё раз или отправьте /cancel.",
                )
                return
            self.inputs.pop(user, None)
            result = self.request_api(
                user,
                "POST",
                f"/auth/{pending.action}",
                {"telegram_id": user, "password": value},
                auth=True,
            )
            if result is not None:
                self.sessions[user] = (
                    result["access_token"],
                    monotonic() + SESSION_TTL,
                )
                self.send(user, "Вы вошли в трекер привычек.", build_main_menu())
            return
        if not pending or not self.get_token(user):
            self.handle_action(user, "menu")
            return
        if pending.action == "delete":
            self.send(user, "Подтвердите удаление кнопкой или отправьте /cancel.")
            return
        value = value.strip()
        if pending.action == "name":
            if not 1 <= len(value) <= 120:
                self.send(user, "Введите название длиной от 1 до 120 символов.")
                return
            self.prompt(
                user,
                "target",
                "Сколько раз нужно выполнить привычку? Введите целое число от 1.",
                pending.habit_id,
                value,
            )
            return
        if pending.action == "target":
            if not (
                value.isascii()
                and value.isdigit()
                and len(value) <= 10
                and 1 <= int(value) <= 2_147_483_647
            ):
                self.send(user, "Введите целое число от 1 до 2147483647.")
                return
            self.save_habit(user, pending, int(value))
            return
        else:
            result = self.request_api(
                user, "PATCH", "/settings", {pending.action: value}
            )
        if result is not None:
            self.inputs.pop(user, None)
            self.send(user, "Изменения сохранены.", build_main_menu())

    def save_habit(self, user, pending, target):
        habit_id = pending.habit_id
        result = self.request_api(
            user,
            "PATCH" if habit_id else "POST",
            f"/habits/{habit_id}" if habit_id else "/habits",
            {"name": pending.name, "target": target},
        )
        if result is not None:
            self.inputs.pop(user, None)
            self.send(user, "Привычка сохранена.", build_main_menu())

    def handle_callback(self, query):
        if (
            not query.message
            or query.message.chat.type != "private"
            or query.message.chat.id != query.from_user.id
        ):
            return
        try:
            self.bot.answer_callback_query(query.id)
        except telebot.apihelper.ApiTelegramException:
            pass
        self.handle_action(query.from_user.id, query.data or "")


def is_valid_habit_id(value):
    return value.isascii() and value.isdigit()
