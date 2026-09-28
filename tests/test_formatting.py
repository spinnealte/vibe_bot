from datetime import date, time
from types import SimpleNamespace

import pytest

from stubbot.db.enums import PriceUnit
from stubbot.services.enrollments import fixed_group_seats, seat_choices
from stubbot.tg.render import split_html
from stubbot.utils.dates import format_day, format_range, format_time_range
from stubbot.utils.money import format_rub


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2026, 10, 13), date(2026, 10, 13), "13 октября"),
        (date(2026, 10, 13), date(2026, 10, 14), "13–14 октября"),
        (date(2026, 9, 30), date(2026, 10, 2), "30 сентября – 2 октября"),
        (date(2026, 12, 30), date(2027, 1, 2), "30 декабря 2026 – 2 января 2027"),
    ],
)
def test_format_range(start: date, end: date, expected: str) -> None:
    assert format_range(start, end) == expected


def test_format_day_and_time() -> None:
    assert format_day(date(2026, 10, 13)) == "13 октября (вт)"
    assert format_time_range(time(10), time(18)) == "10:00–18:00"
    assert format_time_range(time(10), None) == "с 10:00"
    assert format_time_range(None, None) is None


@pytest.mark.parametrize(
    ("kopecks", "expected"),
    [(4_500_000, "45 000 ₽"), (99_950, "999,50 ₽"), (0, "0 ₽"), (123_456_700, "1 234 567 ₽")],
)
def test_format_rub(kopecks: int, expected: str) -> None:
    assert format_rub(kopecks) == expected


def _price(unit: PriceUnit, min_seats: int = 1, max_seats: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(unit=unit, min_seats=min_seats, max_seats=max_seats)


def test_seats_for_prices() -> None:
    pair = _price(PriceUnit.PER_GROUP, 2, 2)
    assert fixed_group_seats(pair) == 2
    assert fixed_group_seats(_price(PriceUnit.PER_PERSON)) is None
    assert fixed_group_seats(None) is None
    assert seat_choices(None) == [1, 2, 3, 4, 5]
    assert seat_choices(_price(PriceUnit.PER_PERSON)) == [1, 2, 3, 4, 5]
    assert seat_choices(_price(PriceUnit.PER_GROUP, 3, 6)) == [3, 4, 5, 6]
    assert seat_choices(_price(PriceUnit.PER_PERSON, 1, 50)) == list(range(1, 11))  # не больше MAX_SEATS


def test_split_html_respects_limit_and_paragraphs() -> None:
    text = "\n\n".join(f"<b>Модуль {i}</b> " + "текст " * 40 for i in range(30))
    chunks = split_html(text, limit=1000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "\n\n".join(chunks) == text  # ничего не потеряли
