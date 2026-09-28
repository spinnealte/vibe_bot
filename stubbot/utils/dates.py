from datetime import date, datetime, time
from zoneinfo import ZoneInfo

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def local_today(timezone: str) -> date:
    """Сегодня по часовому поясу центра (Europe/Moscow), а не сервера."""
    return datetime.now(ZoneInfo(timezone)).date()


def format_day(day: date) -> str:
    """12 октября (сб)"""
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]} ({WEEKDAYS_SHORT[day.weekday()]})"


def format_range(start: date, end: date) -> str:
    """12 октября · 12–13 октября · 30 сентября – 2 октября · 30 декабря 2026 – 2 января 2027"""
    if start == end:
        return f"{start.day} {MONTHS_GENITIVE[start.month - 1]}"
    if start.year != end.year:
        return (f"{start.day} {MONTHS_GENITIVE[start.month - 1]} {start.year} – "
                f"{end.day} {MONTHS_GENITIVE[end.month - 1]} {end.year}")
    if start.month == end.month:
        return f"{start.day}–{end.day} {MONTHS_GENITIVE[end.month - 1]}"
    return f"{start.day} {MONTHS_GENITIVE[start.month - 1]} – {end.day} {MONTHS_GENITIVE[end.month - 1]}"


def format_range_with_year(start: date, end: date) -> str:
    """13–14 октября 2026 (год один раз в конце, если не меняется)"""
    text = format_range(start, end)
    return text if start.year != end.year else f"{text} {end.year}"


def format_short(day: date) -> str:
    """12.10"""
    return day.strftime("%d.%m")


def format_time_range(start: time | None, end: time | None) -> str | None:
    if start and end:
        return f"{start:%H:%M}–{end:%H:%M}"
    if start:
        return f"с {start:%H:%M}"
    return None
