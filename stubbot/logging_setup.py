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

# Как в старом боте: 2026-01-26 15:01:53,299 | INFO | aiogram.event | Update id=… is handled. Duration 167 ms …
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


class MaskingFormatter(logging.Formatter):
    """Маскирует секреты/ПД в готовой строке (в т.ч. в трейсбеках)."""

    def __init__(self) -> None:
        super().__init__(LOG_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
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

    # aiogram.event пишет «Update id=… is handled. Duration … ms» — оставляем, как в старом боте.
    # SQL-запросы SQLAlchemy не пишем даже на DEBUG: там параметры с ПД.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
