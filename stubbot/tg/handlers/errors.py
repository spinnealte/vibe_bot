import logging

from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import ErrorEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stubbot.services.clients import ClientService

logger = logging.getLogger(__name__)

USER_ERROR_TEXT = "Что-то пошло не так. Попробуйте ещё раз чуть позже."


# Регистрируется на Dispatcher (dp.errors), а не на дочернем роутере: ошибки дочерних роутеров
# всплывают только к родителям, соседний роутер их не видит.
async def on_error(event: ErrorEvent, session_factory: async_sessionmaker[AsyncSession]) -> bool:
    """Единая точка для необработанных исключений. В лог — тип апдейта и update_id, без содержимого (там ПД)."""
    exc = event.exception
    update = event.update
    update_type = update.event_type

    if isinstance(exc, (TelegramNetworkError, TelegramRetryAfter)):
        # Ретраи уже исчерпаны в RetryRequestMiddleware — вероятно, лежит туннель.
        logger.warning("Сеть: %s при обработке %s #%s", type(exc).__name__, update_type, update.update_id)
        return True
    if isinstance(exc, TelegramForbiddenError):
        logger.info("Бот заблокирован пользователем (%s #%s)", update_type, update.update_id)
        await _mark_bot_blocked(event, session_factory)
        return True

    logger.error("Ошибка при обработке %s #%s", update_type, update.update_id, exc_info=exc)
    await _notify_user(event)
    return True


async def _mark_bot_blocked(event: ErrorEvent, session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Транзакция апдейта к этому моменту откачена — отметку пишем в своей, короткой."""
    user = getattr(event.update.event, "from_user", None)
    if user is None:
        return
    try:
        async with session_factory() as session, session.begin():
            await ClientService(session).mark_bot_blocked(user.id)
    except Exception:
        logger.warning("Не удалось отметить блокировку бота (#%s)", event.update.update_id, exc_info=True)


async def _notify_user(event: ErrorEvent) -> None:
    update = event.update
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(USER_ERROR_TEXT, show_alert=True)
        elif update.message is not None:
            await update.message.answer(USER_ERROR_TEXT)
    except Exception:
        # Сообщить пользователю не удалось — исходная ошибка уже в логе, вторую не раскручиваем.
        logger.warning("Не удалось отправить сообщение об ошибке (#%s)", update.update_id)
