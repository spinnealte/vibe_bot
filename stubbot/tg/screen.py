"""Показ «экрана» в том же сообщении, где нажали inline-кнопку.

Правила (как в старом боте):
    фото → фото    — edit_media (фото + подпись + клавиатура)
    текст → текст  — edit_text
    фото ↔ текст   — старое сообщение удаляем, присылаем новое
Если сообщение уже удалено пользователем или слишком старое для правки — присылаем новое.

Курсы без фото показываются с заглушкой assets/default_cover.png: после первой загрузки её file_id кэшируется,
и дальше Telegram берёт картинку со своих серверов.
"""

import logging
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message

logger = logging.getLogger(__name__)

DEFAULT_COVER_PATH = Path(__file__).resolve().parents[2] / "assets" / "default_cover.png"


class _DefaultCover:
    """Маркер: «фото курса не загружено — показать заглушку»."""


DEFAULT_COVER = _DefaultCover()
_default_cover_file_id: str | None = None

Photo = str | _DefaultCover | None  # file_id, заглушка или None (текстовый экран)


async def show_screen(message: Message, text: str, markup: InlineKeyboardMarkup | None = None,
                      photo: Photo = None) -> Message:
    """Показать экран вместо сообщения `message`. Возвращает сообщение, где экран оказался."""
    media = _resolve(photo)
    try:
        if media is not None and message.photo:
            result = await message.edit_media(InputMediaPhoto(media=media, caption=text), reply_markup=markup)
            return _remember(result if isinstance(result, Message) else message, photo)
        if media is None and message.text is not None:
            result = await message.edit_text(text, reply_markup=markup)
            return result if isinstance(result, Message) else message
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            return message
        # Сообщение удалено, слишком старое или тип не подходит — ниже пришлём новое.
        logger.debug("Правка экрана не удалась: %s", exc.message)

    await delete_quietly(message)
    return await send_screen(message, text, markup, photo)


async def send_screen(message: Message, text: str, markup: InlineKeyboardMarkup | None = None,
                      photo: Photo = None) -> Message:
    """Новое сообщение-экран в тот же чат (например, после текста, который написал пользователь)."""
    media = _resolve(photo)
    if media is None:
        return await message.answer(text, reply_markup=markup)
    return _remember(await message.answer_photo(media, caption=text, reply_markup=markup), photo)


async def delete_quietly(message: Message) -> None:
    """Удалить сообщение бота; если нельзя (старше 48 ч или уже удалено) — хотя бы убрать кнопки."""
    try:
        await message.delete()
    except TelegramBadRequest:
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


async def strip_keyboard(message: Message, message_id: int) -> None:
    """Убрать inline-кнопки у сообщения бота в этом чате (например, у вопроса, на который ответили текстом)."""
    try:
        await message.bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=message_id, reply_markup=None)
    except TelegramBadRequest:
        pass


def _resolve(photo: Photo) -> str | FSInputFile | None:
    if isinstance(photo, _DefaultCover):
        return _default_cover_file_id or FSInputFile(DEFAULT_COVER_PATH)
    return photo


def _remember(message: Message, photo: Photo) -> Message:
    global _default_cover_file_id
    if isinstance(photo, _DefaultCover) and message.photo:
        _default_cover_file_id = message.photo[-1].file_id
    return message
