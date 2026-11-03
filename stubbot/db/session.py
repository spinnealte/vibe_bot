from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def connect_args(schema: str | None) -> dict:
    """Своя схема в базе (DB_SCHEMA): все таблицы ищутся и создаются в ней - search_path на соединение."""
    return {"server_settings": {"search_path": schema}} if schema else {}


def create_engine(url: URL, schema: str | None = None) -> AsyncEngine:
    # hide_parameters: значения запроса (ФИО, телефон…) не попадают в текст ошибок БД, а значит и в логи.
    return create_async_engine(url, pool_pre_ping=True, hide_parameters=True, connect_args=connect_args(schema))


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
