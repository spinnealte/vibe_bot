"""Запуск: python -m stubbot (из корня stubBot). Остановка: Ctrl+C (локально) или SIGTERM от systemd (сервер)."""

import asyncio
import logging
import sys

from stubbot.config import get_settings
from stubbot.db.session import create_engine, create_session_factory
from stubbot.logging_setup import setup_logging
from stubbot.tg.bot import check_telegram, create_bot
from stubbot.tg.dispatcher import create_dispatcher

logger = logging.getLogger("stubbot")


async def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)

    engine = create_engine(settings.database_url)
    bot = create_bot(settings)
    dp = create_dispatcher(settings, create_session_factory(engine))
    try:
        try:
            await check_telegram(bot)
        except Exception as exc:
            logger.error("Нет связи с Telegram API (%s). Проверьте туннель: deploy/tunnel/check-tunnel.sh", type(exc).__name__)
            return 1
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        return 0
    except asyncio.CancelledError:
        # Ctrl+C: asyncio.run отменяет main(), polling прерывается. Ниже — штатное закрытие соединений.
        logger.info("Получена команда остановки (Ctrl+C)")
        raise
    finally:
        await dp.storage.close()
        await bot.session.close()
        await engine.dispose()
        logger.info("Бот остановлен, соединения закрыты")


def run() -> int:
    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        # Остановка уже залогирована в main(); здесь только гасим трейсбек.
        return 0


if __name__ == "__main__":
    sys.exit(run())
