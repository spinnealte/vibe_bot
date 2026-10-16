"""Запуск бота на Amvera (PaaS): пауза → миграции → начальные справочники на пустой базе → бот.

На Amvera нет консоли для разовых команд, поэтому то, что на своём сервере делает deploy/install.sh,
здесь выполняется при каждом старте. Это безопасно: уже применённые миграции повторно не выполняются,
а справочники ставятся только на пустую базу.

Указан в amvera.yml как run.scriptName. На своём компьютере не нужен: там - python -m stubbot.
"""

import asyncio
import os
import subprocess
import sys
import time

# Папка с кодом на Amvera сбрасывается при каждом обновлении - логи живут в постоянном /data.
# Задаётся до чтения настроек; значение из переменных проекта, если оно есть, остаётся в силе.
# Тексты согласий - в коде (stubbot/services/legal.py), новую версию бот заводит в базе сам при запуске.
os.environ.setdefault("LOG_DIR", "/data/logs")

DEFAULT_START_DELAY = 5.0


def log(message: str) -> None:
    print(f"[старт] {message}", flush=True)


async def database_is_empty() -> bool:
    """Справочник специальностей пуст - значит, базу только что создали."""
    from sqlalchemy import text

    from stubbot.config import get_settings
    from stubbot.db.session import create_engine

    engine = create_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            return (await connection.scalar(text("SELECT count(*) FROM specialties"))) == 0
    finally:
        await engine.dispose()


def migrate() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


def seed_database() -> None:
    from scripts.seed import seed

    asyncio.run(seed())


def run_bot() -> int:
    from stubbot.__main__ import run

    return run()


def main() -> int:
    # При перезапуске Amvera несколько секунд держит старый и новый экземпляры вместе,
    # а двум экземплярам сразу Telegram отвечает ошибкой - даём прежнему остановиться.
    delay = float(os.environ.get("START_DELAY", DEFAULT_START_DELAY))
    log(f"пауза {delay:g} с, чтобы прежний экземпляр успел остановиться")
    time.sleep(delay)

    log("миграции базы")
    migrate()

    # Только на пустой базе: повторный запуск вернул бы специальности, которые админ переименовал или убрал.
    if asyncio.run(database_is_empty()):
        log("база пустая - ставлю начальные справочники и документы согласий")
        seed_database()

    log("запускаю бота")
    return run_bot()


if __name__ == "__main__":
    sys.exit(main())
