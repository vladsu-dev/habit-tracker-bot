import logging
import os
import time
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from requests import RequestException
from sqlalchemy import BigInteger, Date, ForeignKey, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column
from telebot import TeleBot, types
from telebot.apihelper import ApiException, ApiTelegramException

from app.accounts import User
from app.database import Base, SessionFactory
from app.habits.service import list_habit_rows

logger = logging.getLogger(__name__)


class ReminderDelivery(Base):
    __tablename__ = "reminder_deliveries"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    retry_after: Mapped[int] = mapped_column(BigInteger, default=0)
    finished: Mapped[bool] = mapped_column(default=False)


def send_daily_reminder(
    session: Session, user_id: int, bot: TeleBot, now: datetime
) -> None:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        return
    local_now = now.astimezone(ZoneInfo(user.timezone))
    if local_now.time() < user.reminder_time:
        return
    day = local_now.date()
    delivery = session.get(ReminderDelivery, (user.id, day))
    timestamp = int(now.timestamp())
    if delivery and (delivery.finished or delivery.retry_after > timestamp):
        return
    daily_habits = list_habit_rows(session, user, day)
    pending = [habit for habit in daily_habits if habit["completed"] is None]
    if not pending:
        return
    if delivery is None:
        delivery = ReminderDelivery(user_id=user.id, day=day)
        session.add(delivery)

    keyboard = types.InlineKeyboardMarkup()
    message = "Пора отметить привычки."
    if user.token_digest and (user.token_expires_at or 0) > timestamp:
        lines = [message]
        for number, habit in enumerate(pending[:12], start=1):
            lines.append(f"{number}. {habit['name']}")
            prefix = f"mark:{habit['id']}:{day.isoformat()}:"
            keyboard.row(
                types.InlineKeyboardButton(
                    f"{number}. Выполнил", callback_data=prefix + "1"
                ),
                types.InlineKeyboardButton("Не выполнил", callback_data=prefix + "0"),
            )
        if len(pending) > 12:
            lines.append("Остальные привычки доступны в списке на сегодня.")
        message = "\n".join(lines)
    keyboard.add(types.InlineKeyboardButton("Сегодня", callback_data="today"))
    try:
        bot.send_message(user.telegram_id, message, reply_markup=keyboard, timeout=10)
    except ApiTelegramException as error:
        delivery.finished = error.error_code == 403
        parameters = error.result_json.get("parameters", {})
        delivery.retry_after = timestamp + max(300, parameters.get("retry_after", 0))
        logger.warning("Telegram reminder returned HTTP %s", error.error_code)
    except (ApiException, RequestException):
        delivery.retry_after = timestamp + 300
        logger.warning("Reminder delivery temporarily unavailable")
    else:
        delivery.finished = True
    session.commit()


def run_delivery_cycle(bot: TeleBot) -> None:
    with SessionFactory() as session:
        user_ids = session.scalars(select(User.id)).all()
    for user_id in user_ids:
        try:
            with SessionFactory() as session:
                send_daily_reminder(session, user_id, bot, datetime.now(UTC))
        except (SQLAlchemyError, ZoneInfoNotFoundError, ValueError) as error:
            logger.warning("Recipient reminder failed (%s)", type(error).__name__)


def run_worker() -> None:
    bot = TeleBot(os.environ["TELEGRAM_BOT_TOKEN"], threaded=False)
    while True:
        try:
            run_delivery_cycle(bot)
        except SQLAlchemyError as error:
            logger.error("Reminder cycle failed (%s)", type(error).__name__)
        time.sleep(30)


if __name__ == "__main__":
    run_worker()
