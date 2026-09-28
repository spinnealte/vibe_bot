from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.redis import RedisStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stubbot.config import Settings
from stubbot.tg.handlers import build_root_router
from stubbot.tg.handlers.errors import on_error
from stubbot.tg.middlewares import (
    ClientMiddleware,
    DbSessionMiddleware,
    HandlerLogMiddleware,
    UnhandledLogMiddleware,
)


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    storage: BaseStorage | None = None,
) -> Dispatcher:
    dp = Dispatcher(storage=storage or RedisStorage.from_url(settings.redis_url))
    dp["settings"] = settings  # доступно в хендлерах аргументом `settings`
    dp.errors.register(on_error)

    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    dp.update.outer_middleware(UnhandledLogMiddleware())
    # Клиент нужен только там, где есть отправитель-человек: сообщения и нажатия кнопок.
    dp.message.outer_middleware(ClientMiddleware())
    dp.callback_query.outer_middleware(ClientMiddleware())
    # Inner-middleware на Dispatcher применяются и ко всем вложенным роутерам.
    dp.message.middleware(HandlerLogMiddleware())
    dp.callback_query.middleware(HandlerLogMiddleware())

    dp.include_router(build_root_router())
    return dp
