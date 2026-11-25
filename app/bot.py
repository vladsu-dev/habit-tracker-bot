"""Запуск Telegram-бота: python -m app.bot."""

import logging
import os

import httpx
import telebot

from app.bot_presenter import HabitPresenter


def run_bot():
    # Telegram client errors may contain request details.
    telebot.logger.disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bot = telebot.TeleBot(os.environ["TELEGRAM_BOT_TOKEN"], threaded=False)
    with httpx.Client(base_url=os.environ["API_URL"].rstrip("/"), timeout=10) as client:
        presenter = HabitPresenter(bot, client, os.environ["BOT_SERVICE_KEY"])
        bot.register_message_handler(presenter.handle_message, content_types=["text"])
        bot.register_callback_query_handler(
            presenter.handle_callback, func=lambda _: True
        )
        bot.infinity_polling(
            skip_pending=True, allowed_updates=["message", "callback_query"]
        )


if __name__ == "__main__":
    run_bot()
