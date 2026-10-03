"""Ввод расписания админом текстом: дни со временем, «запись до», цена.

Значения возвращаются в виде, пригодном для FSM (JSON): даты и время — строками ISO.
"""

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

MAX_DAYS = 14
MAX_PRICE_RUB = 10_000_000

_DATE = r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?"
_TIME = r"(\d{1,2})[:.](\d{2})"
_DAY_LINE = re.compile(rf"^{_DATE}(?:\s+{_TIME}\s*[-–—]\s*{_TIME})?$")
_DEADLINE = re.compile(rf"^{_DATE}(?:\s+{_TIME})?$")


def _date(day: str, month: str, year: str | None, today: date) -> date | None:
    """Без года — ближайшая такая дата не раньше сегодняшней. Прошедшая или несуществующая — None."""
    try:
        if year:
            parsed = date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day))
        else:
            parsed = date(today.year, int(month), int(day))
            if parsed < today:
                parsed = date(today.year + 1, int(month), int(day))
    except ValueError:
        return None
    return parsed if parsed >= today else None


def _time(hours: str, minutes: str) -> time | None:
    try:
        return time(int(hours), int(minutes))
    except ValueError:
        return None


def parse_days(raw: str, today: date) -> list[dict[str, str | None]] | None:
    """«13.10 10:00-18:00» — день на строку, время необязательно. None — строка не разобрана, дата прошла,
    начало не раньше конца или дней больше MAX_DAYS. Дни сортируются по дате и времени."""
    days = []
    for line in filter(None, (line.strip() for line in raw.splitlines())):
        match = _DAY_LINE.match(line)
        if match is None:
            return None
        day, month, year, start_h, start_m, end_h, end_m = match.groups()
        parsed = _date(day, month, year, today)
        if parsed is None:
            return None
        start = end = None
        if start_h is not None:
            start, end = _time(start_h, start_m), _time(end_h, end_m)
            if start is None or end is None or start >= end:
                return None
        days.append({"date": parsed.isoformat(),
                     "start": start.strftime("%H:%M") if start else None,
                     "end": end.strftime("%H:%M") if end else None})
    if not 0 < len(days) <= MAX_DAYS:
        return None
    return sorted(days, key=lambda d: (d["date"], d["start"] or ""))


def format_days(days: list[dict[str, str | None]]) -> str:
    """Обратно в текст — как админ их вводит (для «Сейчас: …» при правке)."""
    lines = []
    for day in days:
        line = date.fromisoformat(day["date"]).strftime("%d.%m.%Y")
        if day.get("start") and day.get("end"):
            line += f" {day['start']}–{day['end']}"
        lines.append(line)
    return "\n".join(lines)


def parse_deadline(raw: str, now: datetime, tz: ZoneInfo) -> str | None:
    """«11.10 18:00» (время необязательно, без него — 23:59) в часовом поясе центра → ISO с поясом.

    None — не разобрано или уже прошло.
    """
    match _DEADLINE.match(raw.strip()):
        case None:
            return None
        case match:
            day, month, year, hours, minutes = match.groups()
    local_now = now.astimezone(tz)
    parsed_date = _date(day, month, year, local_now.date())
    parsed_time = _time(hours, minutes) if hours is not None else time(23, 59)
    if parsed_date is None or parsed_time is None:
        return None
    moment = datetime.combine(parsed_date, parsed_time, tzinfo=tz)
    return moment.isoformat() if moment > local_now else None


def format_deadline(value: str, tz: ZoneInfo) -> str:
    return datetime.fromisoformat(value).astimezone(tz).strftime("%d.%m.%Y %H:%M")


def parse_rubles(raw: str) -> int | None:
    """«45 000», «45000 ₽», «45000 руб» → сумма в копейках. None — не число или вне разумных пределов."""
    digits = re.sub(r"(\s|₽|руб\.?|р\.?)", "", raw.strip().lower())
    if not digits.isdigit():
        return None
    rubles = int(digits)
    return rubles * 100 if 0 < rubles <= MAX_PRICE_RUB else None
