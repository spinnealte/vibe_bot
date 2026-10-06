from datetime import timedelta

from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stubbot.config import Settings
from stubbot.tg.handlers import build_root_router
from stubbot.tg.handlers.errors import on_error
from stubbot.tg.middlewares import (
    ClientMiddleware,
    DbSessionMiddleware,
    HandlerLogMiddleware,
    ThrottlingMiddleware,
    UnhandledLogMiddleware,
)

FSM_TTL = timedelta(days=2)


def create_storage(redis_url: str) -> RedisStorage:
    """Хранилище шагов сценариев. Незаконченный сценарий (шаг регистрации, черновик мастера в админке) Redis
    забывает сам через FSM_TTL после последнего действия: там бывает ФИО до подтверждения, хранить это бессрочно
    незачем. Человек просто начнёт с того шага, который не сохранён в БД."""
    return RedisStorage.from_url(redis_url, state_ttl=FSM_TTL, data_ttl=FSM_TTL)


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    storage: BaseStorage | None = None,
) -> Dispatcher:
    # Обращения одного человека обрабатываются строго по очереди (events_isolation): иначе два быстрых нажатия
    # «Сохранить» идут одновременно, оба видят один и тот же шаг и создают две одинаковые записи.
    # Замки в памяти процесса — бот работает одним процессом. Разные люди друг друга не ждут.
    dp = Dispatcher(storage=storage or create_storage(settings.redis_url), events_isolation=SimpleEventIsolation())
    dp["settings"] = settings  # доступно в хендлерах аргументом `settings`
    dp["session_factory"] = session_factory  # для работы с БД вне транзакции апдейта (обработчик ошибок)
    dp.errors.register(on_error)

    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    dp.update.outer_middleware(UnhandledLogMiddleware())
    # Защита от флуда — раньше всего остального: лишние обращения не доходят до БД.
    if settings.flood_limit > 0:
        throttle = ThrottlingMiddleware(settings.flood_limit, settings.flood_window)
        dp.message.outer_middleware(throttle)
        dp.callback_query.outer_middleware(throttle)
    # Клиент нужен только там, где есть отправитель-человек: сообщения и нажатия кнопок.
    dp.message.outer_middleware(ClientMiddleware())
    dp.callback_query.outer_middleware(ClientMiddleware())
    # Inner-middleware на Dispatcher применяются и ко всем вложенным роутерам.
    dp.message.middleware(HandlerLogMiddleware())
    dp.callback_query.middleware(HandlerLogMiddleware())

    dp.include_router(build_root_router(settings.applications_enabled))
    return dp
