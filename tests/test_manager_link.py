"""Запись через менеджера: ссылка с черновиком сообщения и «О центре»."""

from datetime import date
from types import SimpleNamespace

import pytest

from stubbot.config import Settings
from stubbot.tg import keyboards, texts
from stubbot.tg.render import about_text, manager_draft, question_draft
from stubbot.utils.links import with_draft_text


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://t.me/aquastom_manager", "https://t.me/aquastom_manager?text=%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82%21"),
        ("https://t.me/aquastom_manager/", "https://t.me/aquastom_manager?text=%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82%21"),
        # Не чат по username - черновик не дописываем, ссылку не портим.
        ("https://t.me/aquastom_manager?start=x", "https://t.me/aquastom_manager?start=x"),
        ("https://t.me/+AbCdEf123", "https://t.me/+AbCdEf123"),
        ("tg://resolve?domain=aquastom_manager", "tg://resolve?domain=aquastom_manager"),
        ("https://example.com/manager", "https://example.com/manager"),
    ],
)
def test_with_draft_text(url: str, expected: str) -> None:
    assert with_draft_text(url, "Привет!") == expected


def test_manager_draft_mentions_course_and_dates() -> None:
    program = SimpleNamespace(title="Имплантация")
    session = SimpleNamespace(title_override=None, program=program,
                              start_date=date(2026, 10, 13), end_date=date(2026, 10, 14))
    card = SimpleNamespace(session=session)
    assert manager_draft(card) == "Здравствуйте! Хочу записаться на курс «Имплантация» (13–14 октября 2026)."
    session.start_date = session.end_date = None  # проведение без дат - «даты уточняются»
    assert manager_draft(card) == "Здравствуйте! Интересует курс «Имплантация». Когда ближайший поток?"


MANAGER = "https://t.me/aquastom_manager"
MENU_TOP = [texts.BTN_SCHEDULE, texts.BTN_CABINET]


def _settings(**overrides: object) -> Settings:
    values = {"db_user": "u", "db_password": "p", "db_name": "n", "owner_telegram_id": 1, **overrides}
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (MANAGER, MANAGER),
        ("t.me/aquastom_manager", MANAGER),  # как ссылку видят в Telegram - без https://
        (" @aquastom_manager ", MANAGER),
        ("http://t.me/aquastom_manager", MANAGER),
        ("telegram.me/aquastom_manager", "https://telegram.me/aquastom_manager"),
        ("tg://resolve?domain=aquastom_manager", "tg://resolve?domain=aquastom_manager"),
        ("", None),
        ("   ", None),
    ],
)
def test_manager_link_setting_accepts_short_forms(raw: str, expected: str | None) -> None:
    assert _settings(manager_url=raw).manager_url == expected
    if expected and expected.startswith("https://t.me/"):
        assert with_draft_text(expected, "Привет!").endswith("?text=%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82%21")


def test_manager_link_setting_rejects_not_a_link() -> None:
    with pytest.raises(ValueError, match="MANAGER_URL"):
        _settings(manager_url="менеджер Анна, +7 999 000-00-00")


def test_main_menu_has_manager_button_when_link_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = lambda chat_id: [[b.text for b in row] for row in keyboards.main_menu(chat_id).keyboard]  # noqa: E731
    monkeypatch.setattr(keyboards, "get_settings", lambda: _settings(admin_ids="22", manager_url=MANAGER))
    assert rows(99) == [MENU_TOP, [texts.BTN_ABOUT, texts.BTN_MANAGER]]
    assert rows(22) == [MENU_TOP, [texts.BTN_ABOUT, texts.BTN_MANAGER], [texts.BTN_ADMIN]]  # «Админке» - свой ряд
    # Нет ссылки - нет и кнопки: вести ей было бы некуда.
    monkeypatch.setattr(keyboards, "get_settings", lambda: _settings(admin_ids="22"))
    assert rows(99) == [MENU_TOP, [texts.BTN_ABOUT]]
    assert rows(22) == [MENU_TOP, [texts.BTN_ABOUT, texts.BTN_ADMIN]]


def test_main_menu_can_be_collapsed_and_reopened_by_person(monkeypatch: pytest.MonkeyPatch) -> None:
    """Главное меню человек сворачивает и разворачивает сам значком у поля ввода (не «всегда на виду»).
    Само оно не сворачивается: в режиме one_time на iPhone пропадает и значок. Клавиатуры шагов регистрации -
    всегда на виду."""
    monkeypatch.setattr(keyboards, "get_settings", lambda: _settings())
    menu = keyboards.main_menu(99)
    assert not menu.one_time_keyboard and not menu.is_persistent
    assert keyboards.registration_nav().is_persistent and not keyboards.registration_nav().one_time_keyboard
    assert keyboards.share_phone().is_persistent and not keyboards.share_phone().one_time_keyboard


def test_manager_button_under_program_and_empty_schedule() -> None:
    link = with_draft_text(MANAGER, "Вопрос")
    markup = keyboards.program_pages(5, 2, 0, 3, manager_url=link)
    assert [[b.text for b in row] for row in markup.inline_keyboard] == [
        ["1/3", "▶️"], [texts.BTN_MANAGER], [texts.BTN_BACK_TO_COURSE]]
    assert markup.inline_keyboard[1][0].url == link
    without = keyboards.program_pages(5, 2, 0, 1)  # ссылки нет - только «Назад к курсу»
    assert [b.text for row in without.inline_keyboard for b in row] == [texts.BTN_BACK_TO_COURSE]
    # Под пустым расписанием кнопка называется по делу: «Узнать о ближайших курсах».
    button = keyboards.contact_manager(link, texts.BTN_MANAGER_COURSES).inline_keyboard[0][0]
    assert (button.text, button.url) == (texts.BTN_MANAGER_COURSES, link)


def test_question_draft_takes_what_person_wrote_to_bot() -> None:
    """Человек написал боту вопрос - в чат менеджера он подставится черновиком, набирать заново не придётся."""
    assert question_draft("  Сколько стоит\nкурс по имплантации? ") == "Сколько стоит курс по имплантации?"
    assert question_draft("а" * 201) == texts.MANAGER_DRAFT_ABOUT  # длинное в ссылку не поместится
    assert question_draft("   ") == texts.MANAGER_DRAFT_ABOUT
    assert len(with_draft_text(MANAGER, question_draft("я" * 200))) < 2000  # кириллица в ссылке - в шесть раз длиннее


VENUES = [
    SimpleNamespace(name="Класс <на Невском>", address="Невский пр., 1", map_url="https://yandex.ru/maps/?text=a&b"),
    SimpleNamespace(name="Клиника", address="ул. Мира, 5", map_url=None),
]


def test_about_text_lists_venues_and_manager_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Список площадок в «О центре» - когда задан шаблон строки площадки."""
    monkeypatch.setattr(texts, "ABOUT_VENUES_TITLE", "🏫 <b>Где проходят занятия</b>")
    monkeypatch.setattr(texts, "ABOUT_VENUE", "• <b>{name}</b> - {address}")
    monkeypatch.setattr(texts, "ABOUT_VENUE_MAP", ' · <a href="{url}">на карте</a>')
    text = about_text(VENUES, with_manager=True)
    assert "АкваСтом" in text
    assert "&lt;на Невском&gt;" in text  # данные из БД экранируются
    assert 'href="https://yandex.ru/maps/?text=a&amp;b"' in text
    assert text.count("на карте") == 1
    assert text.endswith(texts.ABOUT_MANAGER_HINT)
    assert texts.ABOUT_MANAGER_HINT not in about_text(VENUES, with_manager=False)  # нет ссылки - нет и подсказки
    assert texts.ABOUT_VENUES_TITLE not in about_text([], with_manager=False)


def test_about_text_without_venue_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Шаблон строки площадки пуст - адреса не показываем: один заголовок («Уточняется»), без пустых строк,
    и неважно, заведены ли площадки в базе."""
    monkeypatch.setattr(texts, "ABOUT_VENUES_TITLE", "🏫 <b>Где проходят занятия</b>\n Уточняется...")
    monkeypatch.setattr(texts, "ABOUT_VENUE", "")
    for venues in (VENUES, []):
        text = about_text(venues, with_manager=True)
        assert "Уточняется..." in text and "Невском" not in text and "Клиника" not in text
        assert "\n\n\n" not in text
        assert text.endswith(texts.ABOUT_MANAGER_HINT)
