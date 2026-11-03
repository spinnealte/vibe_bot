"""Запуск сервера веб-приложения: вместе с ботом (stubbot/__main__.py, WEBAPP_ENABLED=true) или отдельно
(python -m stubbot.web - разработка интерфейса без приёма сообщений ботом)."""

import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import MenuButtonWebApp, WebAppInfo
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stubbot.config import Settings
from stubbot.tg import texts
from stubbot.tg.online_payments import OnlinePayments
from stubbot.web.app import create_web_app

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = ROOT / "webapp" / "dist"  # сборка интерфейса: cd webapp && npm run build
CACHE_DIR = ROOT / "var" / "webapp-cache"  # скачанные из Telegram обложки


async def start_web_app(settings: Settings, session_factory: async_sessionmaker[AsyncSession], bot: Bot | None,
                        payments: OnlinePayments | None) -> web.AppRunner:
    """Поднять сервер на WEBAPP_HOST:WEBAPP_PORT. Остановка - await runner.cleanup()."""
    app = create_web_app(settings, session_factory, bot, payments, static_dir=STATIC_DIR, cache_dir=CACHE_DIR)
    runner = web.AppRunner(app, access_log=None)  # журнал запросов не пишем: в адресах бывают id и подписи
    await runner.setup()
    await web.TCPSite(runner, settings.webapp_host, settings.webapp_port).start()
    built = "" if (STATIC_DIR / "index.html").exists() else " (интерфейс не собран: cd webapp && npm run build)"
    logger.info("Веб-приложение: http://%s:%s%s", settings.webapp_host, settings.webapp_port, built)
    return runner


async def set_menu_button(bot: Bot, settings: Settings) -> None:
    """Кнопка «Приложение» рядом с полем ввода в чате с ботом - когда есть публичный https-адрес (WEBAPP_URL).
    Без адреса кнопку не трогаем: тот же бот может работать и в другой папке (fullbot)."""
    if not settings.webapp_url:
        return
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
            text=texts.BTN_WEBAPP_MENU, web_app=WebAppInfo(url=settings.webapp_url)))
        logger.info("Кнопка приложения в меню бота установлена")
    except TelegramAPIError as error:
        logger.warning("Кнопка приложения в меню бота не установлена: %s", type(error).__name__)
