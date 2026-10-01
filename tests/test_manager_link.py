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
    assert manager_draft(program, card) == "Здравствуйте! Хочу записаться на курс «Имплантация» (13–14 октября 2026)."
    assert "Когда ближайший поток" in manager_draft(program, None)  # курс без дат


def test_about_text_lists_venues_and_manager_hint() -> None:
    venues = [
        SimpleNamespace(name="Класс <на Невском>", address="Невский пр., 1", map_url="https://yandex.ru/maps/?text=a&b"),
        SimpleNamespace(name="Клиника", address="ул. Мира, 5", map_url=None),
    ]
    text = about_text(venues, with_manager=True)
    assert "АкваСтом" in text
    assert "&lt;на Невском&gt;" in text  # данные из БД экранируются
    assert 'href="https://yandex.ru/maps/?text=a&amp;b"' in text
    assert text.count("на карте") == 1
    assert text.endswith(texts.ABOUT_MANAGER_HINT)
    assert texts.ABOUT_MANAGER_HINT not in about_text(venues, with_manager=False)  # нет ссылки — нет и подсказки
    assert texts.ABOUT_VENUES_TITLE not in about_text([], with_manager=False)
