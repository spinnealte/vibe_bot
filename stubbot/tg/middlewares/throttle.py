"""Защита от флуда: не больше `limit` обращений (сообщений и нажатий кнопок) от одного человека за `window` секунд.

Лишние обращения пропускаются ещё до работы с БД; человеку один раз за окно приходит просьба подождать.
Счётчики лежат в памяти процесса: бот работает одним процессом, а после перезапуска начать с нуля не страшно.
"""

import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject

from stubbot.tg import texts

logger = logging.getLogger(__name__)

SWEEP_INTERVAL = 60.0  # как часто выбрасывать из памяти тех, кто давно молчит


class ThrottlingMiddleware(BaseMiddleware):
    """Outer-middleware на message и callback_query (один объект на оба — счёт общий). Ставится раньше остальных."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._hits: dict[int, deque[float]] = {}  # telegram_id → время принятых обращений в текущем окне
        self._warned: dict[int, float] = {}  # telegram_id → когда последний раз просили подождать
        self._swept_at = clock()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        if user is None or chat is None or chat.type != ChatType.PRIVATE:
            return await handler(event, data)  # не личный чат — бот там всё равно не отвечает
        now = self._clock()
        self._sweep(now)
        hits = self._hits.setdefault(user.id, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            await self._ask_to_wait(event, user.id, now)
            return None
        hits.append(now)
        return await handler(event, data)

    async def _ask_to_wait(self, event: TelegramObject, user_id: int, now: float) -> None:
        """Один раз за окно: на каждое лишнее обращение не отвечаем, иначе сами начнём флудить."""
        if now - self._warned.get(user_id, float("-inf")) < self.window:
            return
        self._warned[user_id] = now
        logger.warning("Флуд: пользователь Telegram %s — больше %s обращений за %g с, лишние пропускаются",
                       user_id, self.limit, self.window)
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(texts.FLOOD_WAIT, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(texts.FLOOD_WAIT)
        except TelegramAPIError:
            pass  # не смогли предупредить (бот заблокирован, сеть) — обращение всё равно пропущено

    def _sweep(self, now: float) -> None:
        if now - self._swept_at < SWEEP_INTERVAL:
            return
        self._swept_at = now
        for user_id in [uid for uid, hits in self._hits.items() if not hits or now - hits[-1] >= self.window]:
            del self._hits[user_id]
            self._warned.pop(user_id, None)
