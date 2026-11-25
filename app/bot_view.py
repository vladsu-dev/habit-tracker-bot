"""Клавиатуры и представление списка привычек."""

from telebot import types

PAGE_SIZE = 5
PASSWORD_HINT = (
    "Введите отдельный пароль для этого бота (8–128 символов). "
    "Telegram получит сообщение с паролем. Бот попытается сразу его удалить. "
    "Для отмены отправьте /cancel."
)


def build_keyboard(*rows):
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        markup.row(
            *(
                types.InlineKeyboardButton(title, callback_data=data)
                for title, data in row
            )
        )
    return markup


def build_login_menu():
    return build_keyboard([("Войти", "login"), ("Регистрация", "register")])


def build_main_menu():
    return build_keyboard(
        [("Сегодня", "today"), ("Все привычки", "all")],
        [("Добавить", "add")],
        [("Настройки", "settings"), ("Выйти", "logout")],
    )


def render_habit_list(send, result, page, include_completed):
    items = result["habits"]
    day = result["date"]
    if not items:
        send("Список пуст. Вы можете добавить привычку.", build_main_menu())
        return
    pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
    page = min(max(0, page), pages - 1)
    title = "Все привычки" if include_completed else f"Привычки на {day}"
    send(f"{title} · страница {page + 1} из {pages}")
    for habit in items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
        habit_id = habit["id"]
        state = {True: "выполнено", False: "пропущено", None: "без отметки"}[
            habit["completed"]
        ]
        rows = []
        if habit["count"] < habit["target"] or habit["completed"] is not None:
            rows.append(
                [
                    ("Выполнено", f"mark:{habit_id}:{day}:1"),
                    ("Пропущено", f"mark:{habit_id}:{day}:0"),
                ]
            )
        rows.append(
            [("Изменить", f"edit:{habit_id}"), ("Удалить", f"delete:{habit_id}")]
        )
        buttons = build_keyboard(*rows)
        send(
            f"{habit['name']}\nПрогресс: {habit['count']} из {habit['target']}\nСегодня: {state}",
            buttons,
        )
    navigation = []
    prefix = "allpage" if include_completed else "page"
    if page:
        navigation.append(("Назад", f"{prefix}:{page - 1}"))
    if page + 1 < pages:
        navigation.append(("Дальше", f"{prefix}:{page + 1}"))
    send(
        "Действия",
        build_keyboard(
            *([navigation] if navigation else []),
            [("Добавить", "add"), ("Меню", "menu")],
        ),
    )
