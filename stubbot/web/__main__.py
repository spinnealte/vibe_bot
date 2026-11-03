"""Только веб-приложение, без приёма сообщений ботом: python -m stubbot.web (из корня папки).

Для разработки интерфейса в браузере: WEBAPP_DEV_AUTH=true и локальная база - вход «dev <telegram_id>».
Бот (BOT_TOKEN) нужен для писем админам, обложек из Telegram и файла реквизитов; без токена - без них.
"""

import asyncio
import logging
import sys
from contextlib import suppress
from datetime import UTC, datetime

from stubbot.config import get_settings
from stubbot.db.session import create_engine, create_session_factory
from stubbot.logging_setup import setup_logging
from stubbot.payments import GatewayConfigError, create_gateway
from stubbot.services.legal import LegalTextChanged, publish_documents
from stubbot.tg.bot import create_bot
from stubbot.tg.online_payments import OnlinePayments
from stubbot.web.server import start_web_app

logger = logging.getLogger("stubbot.web")


async def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    try:
        gateway = create_gateway(settings)
    except GatewayConfigError as exc:
        logger.error("Онлайн-оплата не настроена: %s", exc)
        return 1
    engine = create_engine(settings.database_url, settings.db_schema)
    session_factory = create_session_factory(engine)
    bot = create_bot(settings) if settings.bot_token else None
    payments = OnlinePayments(bot, session_factory, settings, gateway) if gateway and bot else None
    task = None
    runner = None
    try:
        try:
            async with session_factory() as session, session.begin():
                await publish_documents(session, datetime.now(UTC))
        except LegalTextChanged as exc:
            logger.error("%s", exc)
            return 1
        runner = await start_web_app(settings, session_factory, bot, payments)
        if payments is not None:
            task = asyncio.create_task(payments.run_forever(), name="online-payments")
        await asyncio.Event().wait()
        return 0
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if runner is not None:
            await runner.cleanup()
        if gateway is not None:
            await gateway.close()
        if bot is not None:
            await bot.session.close()
        await engine.dispose()
        logger.info("Веб-приложение остановлено")


def run() -> int:
    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(run())
