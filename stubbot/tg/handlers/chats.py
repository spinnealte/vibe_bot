"""Бот работает только в личных чатах. Если его добавили в группу или канал — выходит оттуда сам.

Иначе личные данные (кабинет, а у админа — списки и выгрузка клиентов) оказались бы на виду у всей группы.
Сообщения и кнопки из не-личных чатов отсекает фильтр корневого роутера (handlers/__init__.py).
"""

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMemberUpdated

logger = logging.getLogger(__name__)

router = Router(name="chats")

_PRESENT = (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.RESTRICTED)


@router.my_chat_member(F.chat.type != ChatType.PRIVATE)
async def leave_non_private_chat(event: ChatMemberUpdated, bot: Bot) -> None:
    if event.new_chat_member.status not in _PRESENT:
        return  # бота убрали или он уже вышел
    logger.warning("Бота добавили в чат %s (%s) — выходим: он работает только в личных сообщениях",
                   event.chat.id, event.chat.type)
    try:
        await bot.leave_chat(event.chat.id)
    except TelegramAPIError as exc:
        logger.warning("Не удалось выйти из чата %s: %s", event.chat.id, type(exc).__name__)
