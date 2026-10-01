from datetime import date, time
from types import SimpleNamespace

import pytest

from stubbot.db.enums import PriceUnit
from stubbot.services.enrollments import fixed_group_seats, seat_choices
from stubbot.tg.render import session_caption, split_html, visible_length
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


def _card(description: str | None, lecturers: int = 3, program_html: str | None = None) -> SimpleNamespace:
    program = SimpleNamespace(
        title="Имплантация: от планирования до протезирования", short_description="Двухдневный курс с практикой",
        specialties=[SimpleNamespace(title=t) for t in ("Хирургия", "Имплантация")], level=None, duration_hours=16,
        nmo_points=14, description_html=description, program_html=program_html,
        default_format=SimpleNamespace(value="offline"),
        lecturers=[SimpleNamespace(full_name=f"Лектор{i} Иван Иванович") for i in range(lecturers)],
    )
    session = SimpleNamespace(
        title_override=None, program=program, start_date=date(2026, 10, 13), end_date=date(2026, 10, 14),
        venue=SimpleNamespace(name="Учебный класс <на Невском>", address="Невский пр., 1"),
        format=SimpleNamespace(value="offline"),
        days=[SimpleNamespace(start_time=time(10), end_time=time(18)) for _ in range(2)],
        status=SimpleNamespace(value="registration_open"),
    )
    prices = [SimpleNamespace(label="Полный курс", amount=4_500_000, unit=SimpleNamespace(value="per_person"))]
    return SimpleNamespace(session=session, program=program, active_prices=prices, seats_left=5,
                           accepts_applications=True, goes_to_waitlist=False)


@pytest.mark.parametrize(
    "description",
    [None, "Коротко о курсе.", "<b>Очень</b> длинное описание & текст. " * 200],
    ids=["no-description", "short", "huge"],
)
def test_session_caption_fits_telegram_limit(description: str | None) -> None:
    caption = session_caption(_card(description), "11 октября, 18:00")
    assert visible_length(caption) <= 1024
    assert "&lt;на Невском&gt;" in caption  # админские строки экранируются
    if description and len(description) > 1000:
        assert caption.endswith("…")  # длинное описание обрезано по слову, а не посередине тега
        assert caption.count("<b>") == caption.count("</b>")


def test_caption_shows_program_excerpt_about_400_chars() -> None:
    program = "\n".join(f"<b>День {i}.</b> Тема дня {i}: теория, демонстрация и практика на фантомах" for i in range(1, 30))
    caption = session_caption(_card("Описание курса", program_html=program), None)
    assert "Программа курса" in caption and "О курсе" not in caption  # программа важнее описания
    excerpt = caption.split("Программа курса:</b>\n", 1)[1].split("\n<i>👇", 1)[0]
    assert 300 < len(excerpt) <= 400
    assert excerpt.endswith("…") and "\n" in excerpt  # по границе слова, строки программы сохранены
    assert "Полная программа" in caption
    assert visible_length(caption) <= 1024


def test_caption_dates_with_common_time_and_address() -> None:
    caption = session_caption(_card(None), None)
    assert "13–14 октября 2026, 10:00–18:00" in caption  # одинаковое время во все дни — показываем
    assert "Невский пр., 1" in caption
    card = _card(None)
    card.session.days[1].end_time = time(17)  # дни с разным временем — время не пишем
    assert "<b>Даты:</b> 13–14 октября 2026\n" in session_caption(card, None)


def test_caption_hides_seats_in_poster_mode() -> None:
    """Режим афиши (заявки выключены): мест никто не занимает — счётчик не показываем, статус остаётся."""
    assert "свободных мест: 5" in session_caption(_card(None), None)
    poster = session_caption(_card(None), None, show_seats=False)
    assert "свободных мест" not in poster
    assert "Идёт набор" in poster


def test_caption_short_program_shown_whole_without_hint() -> None:
    caption = session_caption(_card(None, program_html="<b>День 1.</b> Теория\n<b>День 2.</b> Практика"), None)
    assert caption.endswith("День 1. Теория\nДень 2. Практика")


def test_split_html_respects_limit_and_paragraphs() -> None:
    text = "\n\n".join(f"<b>Модуль {i}</b>\n• пункт первый\n• пункт второй" for i in range(30))
    chunks = split_html(text, limit=300)
    assert len(chunks) > 1
    assert all(visible_length(chunk) <= 300 for chunk in chunks)
    assert "\n\n".join(chunks) == text  # делим по абзацам и ничего не теряем
    assert all(chunk.count("<b>") == chunk.count("</b>") for chunk in chunks)  # теги не разорваны


def test_split_html_splits_oversized_paragraph_by_lines_and_words() -> None:
    lines = [f"• пункт номер {i} " + "слово " * 10 for i in range(20)]
    huge_line = "очень " * 200
    chunks = split_html("\n".join(lines) + "\n" + huge_line, limit=300)
    assert all(visible_length(chunk) <= 300 for chunk in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(("\n".join(lines) + "\n" + huge_line).split())
