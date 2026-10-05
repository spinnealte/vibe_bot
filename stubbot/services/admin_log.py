"""Журнал действий админа — отдельный файл logs/admin.log (подключается в logging_setup): кто, что и когда сделал.

В строке — Telegram ID админа, раздел и суть действия с номерами записей. Значения полей и данные клиентов
сюда не пишем. Время ставит общий формат лога.
"""

import logging

ADMIN_LOGGER_NAME = "stubbot.admin"

_logger = logging.getLogger(ADMIN_LOGGER_NAME)


def log_admin_action(admin_telegram_id: int | None, section: str, message: str, *args: object) -> None:
    """Пример строки: «Админ 123456789 · курс · #12: изменено поле 'title'»."""
    _logger.info("Админ %s · %s · " + message, admin_telegram_id, section, *args)
