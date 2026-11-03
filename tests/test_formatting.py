from datetime import UTC, date, datetime, time
from types import SimpleNamespace

import pytest

from stubbot.db.enums import PriceUnit, SessionStatus
from stubbot.services.enrollments import is_pair_price
from stubbot.services.schedule import SessionCard
from stubbot.tg.catalog_flow import deadline_text
from stubbot.tg.render import program_caption, session_caption, split_html, visible_length
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


def test_only_pair_tariff_takes_a_colleague() -> None:
    """Записаться можно за себя или вдвоём с коллегой (решение 20.10.2026): групп на любое число людей нет."""
    assert is_pair_price(_price(PriceUnit.PER_GROUP, 2, 2))
    assert not is_pair_price(_price(PriceUnit.PER_PERSON))
    assert not is_pair_price(_price(PriceUnit.PER_GROUP, 3, 6))
    assert not is_pair_price(None)


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
                           accepts_applications=True, goes_to_waitlist=False, shown_status=session.status)


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
    assert "13–14 октября 2026, 10:00–18:00" in caption  # одинаковое время во все дни - показываем
    assert "Невский пр., 1" in caption
    card = _card(None)
    card.session.days[1].end_time = time(17)  # дни с разным временем - время не пишем
    assert "<b>Даты:</b> 13–14 октября 2026\n" in session_caption(card, None)


def test_caption_of_session_without_dates() -> None:
    """Проведение без дат стоит в афише с пометкой «даты уточняются»; остальное - как у обычного."""
    card = _card(None)
    card.session.start_date = card.session.end_date = None
    card.session.days = []
    caption = session_caption(card, None)
    assert "<b>Даты:</b> уточняются\n" in caption
    assert "Невский пр., 1" in caption and "Полный курс" in caption and "Идёт набор" in caption
    assert visible_length(caption) <= 1024


def _real_card(deadline_passed: bool) -> SessionCard:
    """Настоящая карточка проведения «идёт набор, 5 мест» с подписью из тех же данных, что _card."""
    fake = _card(None)
    session = fake.session
    session.status = SessionStatus.REGISTRATION_OPEN
    session.registration_deadline = datetime(2026, 10, 11, 15, 0, tzinfo=UTC)  # 18:00 по Москве
    session.price_options = [SimpleNamespace(**vars(price), is_active=True) for price in fake.active_prices]
    return SessionCard(session, seats_left=5, deadline_passed=deadline_passed)


def test_caption_after_registration_deadline() -> None:
    """Срок «запись до» прошёл - «Запись закрыта», без счётчика мест и строки срока (26.10.2026). Раньше карточка
    писала «Идёт набор · свободных мест: 5», а «Записаться» отвечало «запись закрыта»."""
    open_card = _real_card(deadline_passed=False)
    caption = session_caption(open_card, deadline_text(open_card, "Europe/Moscow"))
    assert "Идёт набор · свободных мест: 5" in caption and "11 октября, 18:00" in caption
    closed_card = _real_card(deadline_passed=True)
    caption = session_caption(closed_card, deadline_text(closed_card, "Europe/Moscow"))
    assert "Запись закрыта" in caption and "Идёт набор" not in caption
    assert "свободных мест" not in caption and "11 октября, 18:00" not in caption


def test_course_library_caption_has_no_dates_or_status() -> None:
    """Карточка курса в админке - запись библиотеки: даты, цены и статус есть только у проведения."""
    caption = program_caption(_card("Описание курса").program)
    assert "Имплантация" in caption and "Лекторы" in caption and "Направления" in caption and "О курсе" in caption
    assert "Даты" not in caption and "Статус" not in caption and "Стоимость" not in caption


def test_course_caption_keeps_wizard_intro_within_limit() -> None:
    """Шаг «Курс» мастера проведения: вопрос над карточкой не пропадает и не выталкивает подпись за лимит -
    место под него отнимается у отрывка программы."""
    intro = "➕ <b>Новое проведение</b> · шаг 1 из 11\n\n<b>Какой курс проводим?</b>\n" + "Листайте стрелками. " * 20
    program = _card("Описание", program_html="<b>День.</b> Тема дня: теория и практика на фантомах. " * 200).program
    caption = program_caption(program, intro)
    assert caption.startswith(intro + "\n\n") and "Имплантация" in caption and "Программа курса" in caption
    assert visible_length(caption) <= 1024
    assert program_caption(program) == program_caption(program, None)  # без вступления - как в разделе «Курсы»


def test_caption_without_nmo_points() -> None:
    """Баллы НМО не показываем (решение 02.10.2026), даже если в БД они есть."""
    caption = session_caption(_card(None), None)
    assert "НМО" not in caption
    assert "16 ак. ч." in caption


def test_caption_hides_seats_in_poster_mode() -> None:
    """Режим афиши (заявки выключены): мест никто не занимает - счётчик не показываем, статус остаётся."""
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


def test_program_title_takes_room_only_on_the_first_page() -> None:
    """Ревью 03.11.2026: место под название курса - только на первой странице, остальные - полные."""
    from types import SimpleNamespace

    from stubbot.tg.render import PROGRAM_PAGE_LIMIT, program_pages

    long_title = "Мукогингивальная хирургия: пластика мягких тканей вокруг зубов и имплантатов"
    block = "<b>Теория</b>\n" + "\n".join(f"• пункт {i} " + "слово " * 12 for i in range(10))
    # Блок длиннее первой страницы (на ней ещё название), но короче полной: раньше он резался на каждой странице.
    assert PROGRAM_PAGE_LIMIT - visible_length(long_title) < visible_length(block) <= PROGRAM_PAGE_LIMIT
    pages = program_pages(SimpleNamespace(title=long_title, program_html="\n\n".join([block, block])))
    assert pages[-1].startswith(block.rstrip())  # не первая страница вмещает блок целиком - список не разрезан
    chunks = split_html("\n\n".join(["а" * 50] * 4), limit=120, first_limit=60)
    assert [visible_length(chunk) for chunk in chunks] == [50, 102, 50]


def test_split_html_splits_oversized_paragraph_by_lines_and_words() -> None:
    lines = [f"• пункт номер {i} " + "слово " * 10 for i in range(20)]
    huge_line = "очень " * 200
    chunks = split_html("\n".join(lines) + "\n" + huge_line, limit=300)
    assert all(visible_length(chunk) <= 300 for chunk in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(("\n".join(lines) + "\n" + huge_line).split())
