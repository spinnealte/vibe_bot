"""Админка без БД и Telegram: доступ, проверка полей справочников, экраны."""

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.types import User

from stubbot.config import Settings
from stubbot.services.admin_dictionaries import SPECS, DictField, DictKind, FieldKind, parse_field
from stubbot.tg import admin_views, keyboards, texts
from stubbot.tg.filters import IsAdmin


def _settings(**overrides: object) -> Settings:
    values = {"db_user": "u", "db_password": "p", "db_name": "n", "owner_telegram_id": 1, **overrides}
    return Settings(_env_file=None, **values)


def test_admin_ids_from_env_string() -> None:
    settings = _settings(admin_ids="22, 33,")
    assert settings.admin_ids == [22, 33]
    assert settings.is_admin(1)  # owner — всегда админ
    assert settings.is_admin(33)
    assert not settings.is_admin(44)
    assert _settings(admin_ids="").admin_ids == []


def test_admin_button_only_for_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyboards, "get_settings", lambda: _settings(admin_ids="22"))
    buttons = lambda chat_id: [b.text for row in keyboards.main_menu(chat_id).keyboard for b in row]  # noqa: E731
    assert texts.BTN_ADMIN in buttons(22)
    assert texts.BTN_ADMIN in buttons(1)
    assert texts.BTN_ADMIN not in buttons(99)


def test_is_admin_filter() -> None:
    settings = _settings(admin_ids="22")
    check = lambda user_id: asyncio.run(IsAdmin()(  # noqa: E731
        None, settings=settings, event_from_user=User(id=user_id, is_bot=False, first_name="T")))
    assert check(22) and check(1)
    assert not check(99)
    assert not asyncio.run(IsAdmin()(None, settings=settings, event_from_user=None))


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        (DictField("x", FieldKind.LINE, True, 20), "  Учебный   класс ", "Учебный класс"),
        (DictField("x", FieldKind.LINE, True, 5), "слишком длинно", None),
        (DictField("x", FieldKind.LINE, True, 20), "   ", None),
        (DictField("x", FieldKind.URL, False, 100), " https://yandex.ru/maps/-/C ", "https://yandex.ru/maps/-/C"),
        (DictField("x", FieldKind.URL, False, 100), "yandex.ru/maps", None),
        (DictField("x", FieldKind.URL, False, 100), "https://a.ru/b c", None),
        (DictField("x", FieldKind.FULL_NAME, True, 300), "Иванов  Сергей", "Иванов Сергей"),
        (DictField("x", FieldKind.FULL_NAME, True, 300), "Иванов", None),
        (DictField("x", FieldKind.HTML, False, 30), "<b>Хирург</b>\nстаж 20 лет", "<b>Хирург</b>\nстаж 20 лет"),
    ],
)
def test_parse_field(field: DictField, raw: str, expected: str | None) -> None:
    assert parse_field(field, raw) == expected


def _item(item_id: int, title: str, active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(id=item_id, title=title, is_active=active)


def _buttons(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def test_dict_list_pages_and_archive_mark() -> None:
    spec = SPECS[DictKind.SPECIALTIES]
    items = [_item(i, f"Специальность {i}") for i in range(1, 10)] + [_item(10, "Старая", active=False)]
    text, markup = admin_views.dict_list(spec, items, page=0)
    assert "Всего: <b>10</b>" in text and "в архиве: 1" in text
    first = _buttons(markup)
    assert first.count("➕ Добавить") == 1 and "1/2" in first and "▶️" in first and "◀️" not in first
    _, markup = admin_views.dict_list(spec, items, page=1)
    second = _buttons(markup)
    assert "🗄 Старая" in second and "◀️" in second and "▶️" not in second
    text, markup = admin_views.dict_list(spec, [], page=0)
    assert "Пока пусто" in text and "1/1" not in _buttons(markup)


def test_card_escapes_values_and_keeps_bio_html() -> None:
    spec = SPECS[DictKind.LECTURERS]
    item = SimpleNamespace(id=5, full_name="Иванов <Сергей>", regalia=None, bio_html="<b>Хирург</b>", is_active=False)
    text, markup = admin_views.card(spec, item)
    assert "Иванов &lt;Сергей&gt;" in text
    assert "<b>Регалии:</b> —" in text
    assert "<b>Биография:</b> \n<b>Хирург</b>" in text  # HTML из Telegram — как есть
    assert texts.ADMIN_CARD_ARCHIVED in text
    buttons = _buttons(markup)
    assert "✏️ ФИО" in buttons and texts.BTN_ADMIN_RESTORE in buttons and texts.BTN_ADMIN_ARCHIVE not in buttons


def test_field_prompt_buttons() -> None:
    spec = SPECS[DictKind.VENUES]
    required, optional = spec.field("name"), spec.field("map_url")
    assert _buttons(admin_views.field_prompt(spec, required, "h", None)[1]) == [texts.BTN_ADMIN_CANCEL]
    assert _buttons(admin_views.field_prompt(spec, optional, "h", None)[1]) == [texts.BTN_ADMIN_SKIP,
                                                                                 texts.BTN_ADMIN_CANCEL]
    # Правка: «Очистить» — только если сейчас что-то есть.
    assert _buttons(admin_views.field_prompt(spec, optional, "h", "https://a.ru", item_id=3)[1]) == [
        texts.BTN_ADMIN_CLEAR, texts.BTN_ADMIN_CANCEL]
    assert _buttons(admin_views.field_prompt(spec, optional, "h", None, item_id=3)[1]) == [texts.BTN_ADMIN_CANCEL]
