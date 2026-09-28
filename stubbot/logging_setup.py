import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Маскируем то, что не должно попасть в логи, даже если случайно окажется в сообщении или трейсбеке.
# Порядок важен: учётные данные в URL — до email, иначе «pass@host» примется за адрес почты.
_MASKS = [
    (re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}"), "<bot-token>"),
    (re.compile(r"(\w+://)[^:/@\s]+:[^@/\s]+@"), r"\1<credentials>@"),
    (re.compile(r"(?<![\w+])\+?[78][\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)"), "<phone>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
]

DATE_FORMAT = "%d.%m.%Y %H:%M:%S"
# Общие логи: дата | уровень | модуль | сообщение.
GENERAL_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
# Строка на каждый вызов хендлера (HandlerLogMiddleware): процесс | хендлер | дата.время | результат.
HANDLER_FORMAT = "%(process_name)s | %(handler_name)s | %(asctime)s | %(message)s"


class MaskingFormatter(logging.Formatter):
    """Выбирает формат по типу записи и маскирует секреты/ПД в готовой строке."""

    def __init__(self) -> None:
        super().__init__(GENERAL_FORMAT, DATE_FORMAT)
        self._handler_formatter = logging.Formatter(HANDLER_FORMAT, DATE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        if hasattr(record, "handler_name"):
            text = self._handler_formatter.format(record)
        else:
            text = super().format(record)
        for pattern, replacement in _MASKS:
            text = pattern.sub(replacement, text)
        return text


def setup_logging(level: str, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = MaskingFormatter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file = RotatingFileHandler(log_dir / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    root.addHandler(console)
    root.addHandler(file)

    # Не шумим служебными логами библиотек на INFO.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
