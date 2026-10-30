"""Сквозные проверки на локальной PostgreSQL из .env - во временной схеме, которую создают миграции Alembic и
которая удаляется после прогона. Чужие таблицы (демо-данные stubbot_full) не трогаются. База не локальная или
недоступна - проверки пропускаются (на сервере их не запускают)."""

import asyncio
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path

import asyncpg
import pytest
import sqlalchemy.ext.asyncio as sa_asyncio
from alembic import command
from alembic.config import Config

from stubbot.config import Settings, get_settings
from stubbot.payments import LOCAL_DB_HOSTS
from tests.e2e.harness import ADMIN_ID, HANDLERS_CALLED, MANAGER_URL, OWNER_ID, Harness, all_handlers

ROOT = Path(__file__).resolve().parents[2]

# Настройки бота в сценариях (поверх .env: из него берётся только подключение к базе).
E2E_ENV = {
    "APPLICATIONS_ENABLED": "true",
    "ONLINE_PAYMENTS": "fake",
    "MANAGER_URL": MANAGER_URL,
    "OWNER_TELEGRAM_ID": str(OWNER_ID),
    "ADMIN_IDS": str(ADMIN_ID),
    "FLOOD_LIMIT": "0",  # сценарий нажимает быстрее человека; защиту от флуда проверяет test_security
    "TIMEZONE": "Europe/Moscow",
    "YOOKASSA_RECEIPTS": "false",
}


def _connection_settings() -> Settings:
    get_settings.cache_clear()
    settings = Settings()
    if settings.db_host not in LOCAL_DB_HOSTS:
        pytest.skip("сквозные проверки - только на локальной базе")
    return settings


async def _execute(settings: Settings, sql: str) -> None:
    conn = await asyncpg.connect(host=settings.db_host, port=settings.db_port, user=settings.db_user,
                                 password=settings.db_password.get_secret_value(), database=settings.db_name,
                                 timeout=5)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


def run_alembic(schema: str, action: Callable[[Config], None]) -> None:
    """Команда Alembic (migrations/env.py как есть) во временной схеме: движок получает search_path."""
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    original = sa_asyncio.create_async_engine

    def engine_in_schema(url, **kwargs):
        kwargs.setdefault("connect_args", {})["server_settings"] = {"search_path": schema}
        return original(url, **kwargs)

    sa_asyncio.create_async_engine = engine_in_schema
    try:
        action(config)
    finally:
        sa_asyncio.create_async_engine = original


def new_schema(settings: Settings, prefix: str = "e2e") -> str:
    schema = f"{prefix}_{os.getpid()}_{secrets.token_hex(3)}"
    try:
        asyncio.run(_execute(settings, f'CREATE SCHEMA "{schema}"'))
    except (OSError, asyncpg.PostgresError, TimeoutError) as error:
        pytest.skip(f"локальная база недоступна: {type(error).__name__}")
    return schema


def drop_schema(settings: Settings, schema: str) -> None:
    assert schema.startswith(("e2e_", "mig_")), schema  # удаляем только свою временную схему
    asyncio.run(_execute(settings, f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@pytest.fixture(scope="session")
def e2e_schema() -> str:
    settings = _connection_settings()
    schema = new_schema(settings)
    try:
        run_alembic(schema, lambda config: command.upgrade(config, "head"))
        yield schema
    finally:
        drop_schema(settings, schema)
        get_settings.cache_clear()


class E2E:
    def __init__(self, schema: str) -> None:
        self.schema = schema

    def run(self, scenario: Callable[[Harness], Awaitable[None]]) -> None:
        async def main() -> None:
            async with Harness(get_settings(), self.schema) as h:
                await scenario(h)

        asyncio.run(main())


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """После прогона: какие хендлеры бота не вызвал ни один сквозной сценарий (их проверяют только модульные тесты)."""
    if not HANDLERS_CALLED:
        return
    handlers = all_handlers()
    missed = sorted(handlers - HANDLERS_CALLED)
    terminalreporter.write_sep("-", f"сквозные сценарии вызвали {len(handlers) - len(missed)} из {len(handlers)} "
                                    "хендлеров бота")
    for name in missed:
        terminalreporter.write_line(f"  не вызван: {name}")


@pytest.fixture
def e2e(e2e_schema: str, monkeypatch: pytest.MonkeyPatch) -> E2E:
    for key, value in E2E_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()  # бот читает настройки и через get_settings() (меню, афиша)
    yield E2E(e2e_schema)
    get_settings.cache_clear()
