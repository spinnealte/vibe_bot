import asyncio
import logging

from aiogram import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.methods import GetUpdates, TelegramMethod
from aiogram.methods.base import Response, TelegramType

logger = logging.getLogger(__name__)


class RetryRequestMiddleware(BaseRequestMiddleware):
    """Повтор запросов к Bot API при кратковременных сбоях (туннель, 5xx, flood control).

    GetUpdates не трогаем: у polling свой цикл переподключения с backoff.
    Оговорка: если запрос дошёл до Telegram, а ответ потерялся, повтор sendMessage даст дубль сообщения.
    Для бота-заглушки это приемлемее, чем потерянное сообщение.
    """

    def __init__(self, attempts: int = 3, base_delay: float = 1.0, max_delay: float = 10.0) -> None:
        self.attempts = attempts
        self.base_delay = base_delay
        self.max_delay = max_delay

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        if isinstance(method, GetUpdates):
            return await make_request(bot, method)

        attempt = 0
        while True:
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                if attempt >= self.attempts:
                    raise
                delay = float(exc.retry_after)
                reason = "flood control"
            except (TelegramNetworkError, TelegramServerError) as exc:
                if attempt >= self.attempts:
                    raise
                delay = min(self.base_delay * 2**attempt, self.max_delay)
                reason = type(exc).__name__
            attempt += 1
            logger.warning(
                "Bot API %s: %s, повтор %d/%d через %.1f с",
                type(method).__name__,
                reason,
                attempt,
                self.attempts,
                delay,
            )
            await asyncio.sleep(delay)
