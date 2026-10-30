"""Символ NUL (\\x00) - убрать из сообщения до хендлеров (ошибка найдена 28.10.2026).

PostgreSQL не хранит его в строках: город, e-mail, запрос поиска, параметр /start с ним уронили бы запрос к базе,
а человек увидел бы «Что-то пошло не так». Попадает он случайно (копирование из других программ) или нарочно.
Убираем из текста, подписи и имени файла; разметку (entities) сдвигаем - форматирование админа остаётся на месте.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, MessageEntity, TelegramObject

NUL = "\x00"


def strip_nul(text: str | None) -> str | None:
    return text.replace(NUL, "") if text else text


def _strip_with_entities(text: str, entities: list[MessageEntity] | None) -> tuple[str, list[MessageEntity] | None]:
    """Текст без NUL и разметка, сдвинутая на убранные символы. Позиции разметки Telegram считает в UTF-16."""
    removed, position = [], 0
    for char in text:
        if char == NUL:
            removed.append(position)
        position += 2 if ord(char) > 0xFFFF else 1
    if not entities:
        return text.replace(NUL, ""), entities
    moved = []
    for entity in entities:
        start, end = entity.offset, entity.offset + entity.length
        inside = sum(start <= p < end for p in removed)
        if entity.length > inside:
            moved.append(entity.model_copy(update={"offset": start - sum(p < start for p in removed),
                                                   "length": entity.length - inside}))
    return text.replace(NUL, ""), moved


class TextCleanupMiddleware(BaseMiddleware):
    """Outer-middleware на message - после защиты от флуда, до клиента и хендлеров."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            update: dict[str, Any] = {}
            if event.text and NUL in event.text:
                update["text"], update["entities"] = _strip_with_entities(event.text, event.entities)
            if event.caption and NUL in event.caption:
                update["caption"], update["caption_entities"] = _strip_with_entities(event.caption,
                                                                                     event.caption_entities)
            if event.document is not None and event.document.file_name and NUL in event.document.file_name:
                update["document"] = event.document.model_copy(
                    update={"file_name": strip_nul(event.document.file_name)})
            if event.contact is not None and NUL in (event.contact.first_name or "") + (event.contact.phone_number or ""):
                update["contact"] = event.contact.model_copy(update={
                    "first_name": strip_nul(event.contact.first_name),
                    "phone_number": strip_nul(event.contact.phone_number)})
            if update:
                event = event.model_copy(update=update)  # сообщения aiogram неизменяемы - подменяем копией
        return await handler(event, data)
