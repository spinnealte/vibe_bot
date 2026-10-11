"""Запись через менеджера: ссылка с черновиком сообщения и «О центре»."""

from datetime import date
from types import SimpleNamespace

import pytest

from stubbot.tg import texts
from stubbot.tg.render import about_text, manager_draft
from stubbot.utils.links import with_draft_text


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://t.me/aquastom_manager", "https://t.me/aquastom_manager?text=%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82%21"),
        ("https://t.me/aquastom_manager/", "https://t.me/aquastom_manager?text=%D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82%21"),
        # Не чат по username — черновик не дописываем, ссылку не портим.
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
    session.start_date = session.end_date = None  # проведение без дат — «даты уточняются»
    assert manager_draft(card) == "Здравствуйте! Интересует курс «Имплантация». Когда ближайший поток?"


VENUES = [
    SimpleNamespace(name="Класс <на Невском>", address="Невский пр., 1", map_url="https://yandex.ru/maps/?text=a&b"),
    SimpleNamespace(name="Клиника", address="ул. Мира, 5", map_url=None),
]


def test_about_text_lists_venues_and_manager_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Список площадок в «О центре» — когда задан шаблон строки площадки."""
    monkeypatch.setattr(texts, "ABOUT_VENUES_TITLE", "🏫 <b>Где проходят занятия</b>")
    monkeypatch.setattr(texts, "ABOUT_VENUE", "• <b>{name}</b> — {address}")
    monkeypatch.setattr(texts, "ABOUT_VENUE_MAP", ' · <a href="{url}">на карте</a>')
    text = about_text(VENUES, with_manager=True)
    assert "АкваСтом" in text
    assert "&lt;на Невском&gt;" in text  # данные из БД экранируются
    assert 'href="https://yandex.ru/maps/?text=a&amp;b"' in text
    assert text.count("на карте") == 1
    assert text.endswith(texts.ABOUT_MANAGER_HINT)
    assert texts.ABOUT_MANAGER_HINT not in about_text(VENUES, with_manager=False)  # нет ссылки — нет и подсказки
    assert texts.ABOUT_VENUES_TITLE not in about_text([], with_manager=False)


def test_about_text_without_venue_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Шаблон строки площадки пуст — адреса не показываем: один заголовок («Уточняется»), без пустых строк,
    и неважно, заведены ли площадки в базе."""
    monkeypatch.setattr(texts, "ABOUT_VENUES_TITLE", "🏫 <b>Где проходят занятия</b>\n Уточняется...")
    monkeypatch.setattr(texts, "ABOUT_VENUE", "")
    for venues in (VENUES, []):
        text = about_text(venues, with_manager=True)
        assert "Уточняется..." in text and "Невском" not in text and "Клиника" not in text
        assert "\n\n\n" not in text
        assert text.endswith(texts.ABOUT_MANAGER_HINT)
