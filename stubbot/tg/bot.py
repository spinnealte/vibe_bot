import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode

from stubbot.config import Settings
from stubbot.tg.middlewares import RetryRequestMiddleware

logger = logging.getLogger(__name__)


def create_bot(settings: Settings) -> Bot:
    if settings.bot_token is None:
        raise RuntimeError("BOT_TOKEN не задан в .env")

    proxy = settings.telegram_proxy_url.get_secret_value() if settings.telegram_proxy_url else None
    session = AiohttpSession(proxy=proxy, timeout=settings.telegram_timeout)
    session.middleware(RetryRequestMiddleware(attempts=settings.telegram_retry_attempts))
    logger.info("Подключение к Telegram: %s", "через прокси" if proxy else "напрямую")

    return Bot(
        token=settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


async def check_telegram(bot: Bot) -> None:
    """Проверка связи при старте. В лог — только результат и @username, без токена и адресов."""
    me = await bot.me()
    logger.info("Связь с Telegram: OK, @%s", me.username)
