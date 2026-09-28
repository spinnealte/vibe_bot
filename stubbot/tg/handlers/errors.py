import logging

from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)

USER_ERROR_TEXT = "Что-то пошло не так. Попробуйте ещё раз чуть позже."


# Регистрируется на Dispatcher (dp.errors), а не на дочернем роутере: ошибки дочерних роутеров
# всплывают только к родителям, соседний роутер их не видит.
async def on_error(event: ErrorEvent) -> bool:
    """Единая точка для необработанных исключений. В лог — тип апдейта и update_id, без содержимого (там ПД)."""
    exc = event.exception
    update = event.update
    update_type = update.event_type

    if isinstance(exc, (TelegramNetworkError, TelegramRetryAfter)):
        # Ретраи уже исчерпаны в RetryRequestMiddleware — вероятно, лежит туннель.
        logger.warning("Сеть: %s при обработке %s #%s", type(exc).__name__, update_type, update.update_id)
        return True
    if isinstance(exc, TelegramForbiddenError):
        # Пользователь заблокировал бота. Флаг clients.is_bot_blocked проставит сервис на этапе 3.
        logger.info("Бот заблокирован пользователем (%s #%s)", update_type, update.update_id)
        return True

    logger.error("Ошибка при обработке %s #%s", update_type, update.update_id, exc_info=exc)
    await _notify_user(event)
    return True


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
