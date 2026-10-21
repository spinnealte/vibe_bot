"""Уведомления о записях и заказах - всем админам из ADMIN_IDS и владельцу (решение 20.10.2026).

Не доставилось кому-то из админов (не начинал диалог с ботом, заблокировал его) - пишем в лог и идём дальше:
запись клиента от этого не страдает. Текст - HTML, собран в admin_orders_views (значения экранированы).
"""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup

from stubbot.config import Settings

logger = logging.getLogger(__name__)


async def notify_admins(bot: Bot, settings: Settings, text: str, markup: InlineKeyboardMarkup | None = None,
                        document: str | None = None, photo: str | None = None) -> None:
    """Сообщение (или файл с подписью: document / photo - file_id) каждому админу."""
    for chat_id in settings.admin_chat_ids:
        try:
            if document is not None:
                await bot.send_document(chat_id, document, caption=text, reply_markup=markup)
            elif photo is not None:
                await bot.send_photo(chat_id, photo, caption=text, reply_markup=markup)
            else:
                await bot.send_message(chat_id, text, reply_markup=markup)
        except (TelegramForbiddenError, TelegramBadRequest) as error:
            logger.warning("Админу %s не доставлено уведомление: %s", chat_id, type(error).__name__)
