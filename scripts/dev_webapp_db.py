"""База для разработки веб-приложения: своя схема (DB_SCHEMA из .env, например webdev) в локальной базе.

Создаёт схему, накатывает миграции, ставит справочники, документы согласий и демо-данные полной версии
(курсы, проведения, клиенты, заказы). Повторный запуск пересоздаёт демо-данные. Только локальная база.

Запуск из корня папки: python -m scripts.dev_webapp_db           - создать / обновить
                       python -m scripts.dev_webapp_db --drop    - удалить схему целиком
"""

import asyncio
import subprocess
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import asyncpg

from scripts import seed, seed_full_demo
from stubbot.config import get_settings
from stubbot.db.session import create_engine, create_session_factory


async def _execute(sql: str) -> None:
    s = get_settings()
    conn = await asyncpg.connect(host=s.db_host, port=s.db_port, user=s.db_user,
                                 password=s.db_password.get_secret_value(), database=s.db_name)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _demo() -> None:
    s = get_settings()
    engine = create_engine(s.database_url, s.db_schema)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            await seed_full_demo.create(session, datetime.now(UTC), ZoneInfo(s.timezone))
    finally:
        await engine.dispose()


def main() -> int:
    s = get_settings()
    if s.db_host not in ("localhost", "127.0.0.1", "::1") or not s.db_schema or s.db_schema == "public":
        print("Только локальная база и своя схема (DB_SCHEMA в .env, не public).")
        return 1
    if "--drop" in sys.argv:
        asyncio.run(_execute(f'DROP SCHEMA IF EXISTS "{s.db_schema}" CASCADE'))
        print(f"Схема {s.db_schema} удалена.")
        return 0
    asyncio.run(_execute(f'CREATE SCHEMA IF NOT EXISTS "{s.db_schema}"'))
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    asyncio.run(seed.seed())
    asyncio.run(_demo())
    print(f"Схема {s.db_schema} готова: миграции, справочники, документы, демо-данные.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
