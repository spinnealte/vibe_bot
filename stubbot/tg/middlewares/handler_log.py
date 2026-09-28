"""Строка лога на каждый апдейт: процесс | хендлер | дата.время | краткий результат.

Пример:
    message /start | start.cmd_start | 28.09.2026 23:31:00 | OK, 12 мс
    callback reg:phone [Registration:phone] | registration.on_phone | 28.09.2026 23:31:05 | OK, 40 мс
    message text | — | 28.09.2026 23:31:09 | не обработано

Текст сообщений пользователя не логируется (там ПД): только команда или тип содержимого.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.dispatcher.event.handler import HandlerObject
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

logger = logging.getLogger("stubbot.handlers")

_MAX_CALLBACK_DATA = 64


def describe_event(event: TelegramObject, raw_state: str | None = None) -> str:
    if isinstance(event, Update):
        event = event.event
    if isinstance(event, Message):
        if event.text and event.text.startswith("/"):
            # Только сама команда: аргументы /start — диплинк, но в общем случае после команды может быть что угодно.
            process = f"message {event.text.split(maxsplit=1)[0]}"
        else:
            process = f"message {getattr(event.content_type, 'value', event.content_type)}"
    elif isinstance(event, CallbackQuery):
        process = f"callback {(event.data or '')[:_MAX_CALLBACK_DATA]}"
    else:
        process = type(event).__name__
    if raw_state:
        process += f" [{raw_state}]"
    return process


def describe_handler(handler: HandlerObject | None) -> str:
    if handler is None:
        return "—"
    callback = handler.callback
    module = getattr(callback, "__module__", "").rsplit(".", 1)[-1]
    name = getattr(callback, "__qualname__", repr(callback))
    return f"{module}.{name}" if module else name


def _log(level: int, process: str, handler: str, result: str) -> None:
    logger.log(level, result, extra={"process_name": process, "handler_name": handler})


class HandlerLogMiddleware(BaseMiddleware):
    """Inner-middleware (message, callback_query): вызывается, только когда хендлер найден."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        process = describe_event(event, data.get("raw_state"))
        handler_name = describe_handler(data.get("handler"))
        started = time.perf_counter()
        try:
            result = await handler(event, data)
        except Exception as exc:
            _log(logging.ERROR, process, handler_name, f"ошибка {type(exc).__name__}, {_elapsed_ms(started)} мс")
            raise
        outcome = "пропущено фильтром" if result is UNHANDLED else "OK"
        _log(logging.INFO, process, handler_name, f"{outcome}, {_elapsed_ms(started)} мс")
        return result


class UnhandledLogMiddleware(BaseMiddleware):
    """Outer-middleware на update: логирует апдейты, для которых не нашлось ни одного хендлера."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        if result is UNHANDLED:
            _log(logging.INFO, describe_event(event, data.get("raw_state")), "—", "не обработано")
        return result


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
