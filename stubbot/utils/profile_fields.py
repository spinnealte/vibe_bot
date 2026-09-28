"""Разбор необязательных полей профиля. Каждая функция: None — ввод не подходит, иначе значение для БД."""

import re
from datetime import date, datetime

_HAS_LETTER = re.compile(r"[A-Za-zА-Яа-яЁё]")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_YEARS = re.compile(r"^\s*(\d{1,2})(?:\s*(?:год|года|лет|г\.?))?\s*$", re.IGNORECASE)
_DATE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y")

MAX_EXPERIENCE_YEARS = 70
MIN_AGE, MAX_AGE = 16, 100


def _collapse(raw: str) -> str:
    return " ".join(raw.split())


def parse_city(raw: str) -> str | None:
    value = _collapse(raw)
    if not 2 <= len(value) <= 128 or not _HAS_LETTER.search(value):
        return None
    return value


def parse_workplace(raw: str) -> str | None:
    value = _collapse(raw)
    if not 2 <= len(value) <= 256:
        return None
    return value


def parse_experience_years(raw: str, today: date) -> int | None:
    """«5», «5 лет» → год начала практики (текущий год − стаж). Храним год: стаж не устаревает."""
    match = _YEARS.match(raw)
    if match is None:
        return None
    years = int(match.group(1))
    if years > MAX_EXPERIENCE_YEARS:
        return None
    return today.year - years


def experience_years(practice_since_year: int | None, today: date) -> int | None:
    return None if practice_since_year is None else max(today.year - practice_since_year, 0)


def parse_email(raw: str) -> str | None:
    value = raw.strip().lower()
    if len(value) > 254 or not _EMAIL.match(value):
        return None
    return value


def parse_birth_date(raw: str, today: date) -> date | None:
    value = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt).date()
        except ValueError:
            continue
        age = today.year - parsed.year - ((today.month, today.day) < (parsed.month, parsed.day))
        return parsed if MIN_AGE <= age <= MAX_AGE else None
    return None
