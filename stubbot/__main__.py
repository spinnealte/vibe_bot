"""Запуск: python -m stubbot (из корня stubBot). Остановка: Ctrl+C (локально) или SIGTERM от systemd (сервер)."""

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
from stubbot.tg.bot import check_telegram, create_bot
from stubbot.tg.dispatcher import create_dispatcher
from stubbot.tg.online_payments import OnlinePayments
from stubbot.tg.reminders import PaymentReminders
from stubbot.web.server import set_menu_button, start_web_app

logger = logging.getLogger("stubbot")


async def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    try:
        gateway = create_gateway(settings)  # онлайн-оплата: None - выключена (ONLINE_PAYMENTS=off)
    except GatewayConfigError as exc:
        logger.error("Онлайн-оплата не настроена: %s", exc)
        return 1

    engine = create_engine(settings.database_url, settings.db_schema)
    session_factory = create_session_factory(engine)
    bot = create_bot(settings)
    online_payments = OnlinePayments(bot, session_factory, settings, gateway) if gateway is not None else None
    dp = create_dispatcher(settings, session_factory, online_payments=online_payments)
    tasks: list[asyncio.Task] = []
    web_runner = None
    try:
        try:
            # Тексты согласий - в коде; новую версию бот заводит в базе сам (на Amvera нет консоли).
            async with session_factory() as session, session.begin():
                await publish_documents(session, datetime.now(UTC))
        except LegalTextChanged as exc:
            logger.error("%s", exc)
            return 1
        try:
            await check_telegram(bot)
        except Exception as exc:
            logger.error("Нет связи с Telegram API (%s). Проверьте туннель: deploy/tunnel/check-tunnel.sh", type(exc).__name__)
            return 1
        if settings.applications_enabled:
            # Полная версия: напоминания об оплате - фоновая задача рядом с приёмом сообщений.
            tasks.append(asyncio.create_task(PaymentReminders(bot, session_factory, settings).run_forever(),
                                             name="payment-reminders"))
        if online_payments is not None:
            # Сверка онлайн-платежей: оплату без нажатия «Проверить» бот заметит в течение минуты.
            tasks.append(asyncio.create_task(online_payments.run_forever(), name="online-payments"))
        if settings.webapp_enabled:
            # Веб-приложение (Mini App) - в том же процессе: тот же бот, база и онлайн-оплата.
            web_runner = await start_web_app(settings, session_factory, bot, online_payments)
            await set_menu_button(bot, settings)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        return 0
    except asyncio.CancelledError:
        # Ctrl+C: asyncio.run отменяет main(), polling прерывается. Ниже - штатное закрытие соединений.
        logger.info("Получена команда остановки (Ctrl+C)")
        raise
    finally:
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if web_runner is not None:
            await web_runner.cleanup()
        if gateway is not None:
            await gateway.close()
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
