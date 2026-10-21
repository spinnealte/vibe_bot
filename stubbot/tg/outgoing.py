"""Сообщение от админа клиенту через бота: текст, файл или фото с подписью (полная версия, этап 1, шаг 4).

Одна функция deliver и для предпросмотра админу, и для отправки клиенту - так админ видит ровно то, что получит
клиент. Текст - HTML, который собрал aiogram из форматирования сообщения админа (message.html_text), он безопасен.

Черновик живёт в FSM словарём; «✅ Отправить» срабатывает один раз (SendOnce): двойное нажатие не отправит дубль.
"""

from collections import OrderedDict
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.models import Client
from stubbot.services.clients import ClientService

TEXT_LIMIT = 4096  # лимиты Telegram на видимый текст
CAPTION_LIMIT = 1024


class OutgoingKind(StrEnum):
    TEXT = "text"
    DOCUMENT = "document"
    PHOTO = "photo"


@dataclass(frozen=True)
class Outgoing:
    kind: OutgoingKind
    text: str  # HTML: текст сообщения или подпись к файлу (может быть пустой)
    file_id: str | None = None

    @property
    def limit(self) -> int:
        return TEXT_LIMIT if self.kind is OutgoingKind.TEXT else CAPTION_LIMIT

    def to_data(self) -> dict[str, Any]:
        return {**asdict(self), "kind": self.kind.value}

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "Outgoing":
        return cls(OutgoingKind(data["kind"]), data["text"], data.get("file_id"))


def from_message(message: Message) -> Outgoing | None:
    """Что прислал админ. None - не текст, не файл и не фото (стикер, голосовое…)."""
    if message.document is not None:
        return Outgoing(OutgoingKind.DOCUMENT, message.html_text if message.caption else "", message.document.file_id)
    if message.photo:
        return Outgoing(OutgoingKind.PHOTO, message.html_text if message.caption else "", message.photo[-1].file_id)
    if message.text:
        return Outgoing(OutgoingKind.TEXT, message.html_text)
    return None


async def deliver(bot: Bot, chat_id: int, outgoing: Outgoing, markup: InlineKeyboardMarkup | None = None) -> Message:
    """Отправить в чат. Ошибки Telegram (бот заблокирован, неверный файл) - вызывающему."""
    caption = outgoing.text or None
    match outgoing.kind:
        case OutgoingKind.DOCUMENT:
            return await bot.send_document(chat_id, outgoing.file_id, caption=caption, reply_markup=markup)
        case OutgoingKind.PHOTO:
            return await bot.send_photo(chat_id, outgoing.file_id, caption=caption, reply_markup=markup)
        case _:
            return await bot.send_message(chat_id, outgoing.text, reply_markup=markup)


class Delivery(StrEnum):
    DELIVERED = "delivered"
    BLOCKED = "blocked"  # клиент заблокировал бота
    FAILED = "failed"  # Telegram не принял (файл, разметка)
    NO_TELEGRAM = "no_telegram"  # клиента нет в боте (коллега, которого записали)


async def deliver_to_client(bot: Bot, session: AsyncSession, client: Client, outgoing: Outgoing) -> Delivery:
    """Сообщение от админа клиенту. Заблокировал бота - отмечаем это у клиента."""
    if client.telegram_id is None:
        return Delivery.NO_TELEGRAM
    try:
        await deliver(bot, client.telegram_id, outgoing)
    except TelegramForbiddenError:
        await ClientService(session).mark_bot_blocked(client.telegram_id)
        return Delivery.BLOCKED
    except TelegramBadRequest:
        return Delivery.FAILED
    return Delivery.DELIVERED


class SendOnce:
    """Ключи черновиков, которые уже ушли. Проверка и запись - без await между ними, поэтому два почти
    одновременных нажатия «Отправить» не пройдут оба. Бот работает одним процессом - памяти процесса хватает."""

    def __init__(self, size: int = 1000) -> None:
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._size = size

    def claim(self, key: str) -> bool:
        """True - ключ впервые: можно отправлять. False - уже отправляли."""
        if key in self._seen:
            return False
        self._seen[key] = None
        if len(self._seen) > self._size:
            self._seen.popitem(last=False)
        return True


send_once = SendOnce()
