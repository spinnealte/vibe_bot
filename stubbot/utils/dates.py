from datetime import date, datetime
from zoneinfo import ZoneInfo


def local_today(timezone: str) -> date:
    """Сегодня по часовому поясу центра (Europe/Moscow), а не сервера."""
    return datetime.now(ZoneInfo(timezone)).date()
