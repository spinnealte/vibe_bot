"""Этап 5: ввод расписания админом, правила полей проведения, экраны карточек — без БД и Telegram."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from stubbot.services.admin_courses import COURSE_SPEC
from stubbot.services.admin_dictionaries import FieldKind, parse_field
from stubbot.services.admin_sessions import SESSION_SPEC, valid_prices
from stubbot.tg import admin_views, texts
from stubbot.utils.money import format_rub
from stubbot.utils.schedule_input import format_days, parse_days, parse_deadline, parse_rubles

TODAY = date(2026, 10, 2)
MSK = ZoneInfo("Europe/Moscow")


def _buttons(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def test_parse_days_lines_year_and_sorting() -> None:
    days = parse_days("14.10 10:00-17:00\n13.10.2026 10:00–18:00\n\n05.01", TODAY)
    assert days == [
        {"date": "2026-10-13", "start": "10:00", "end": "18:00"},
        {"date": "2026-10-14", "start": "10:00", "end": "17:00"},
        {"date": "2027-01-05", "start": None, "end": None},  # без года, уже прошло в этом году → следующий
    ]
    assert format_days(days).splitlines()[0] == "13.10.2026 10:00–18:00"


@pytest.mark.parametrize(
    "raw",
    ["", "завтра", "13.10 18:00-10:00", "31.02", "01.10.2026", "13.10 25:00-26:00", "\n".join(["13.10"] * 15)],
    ids=["empty", "words", "end-before-start", "no-such-date", "past-with-year", "bad-time", "too-many"],
)
def test_parse_days_rejects(raw: str) -> None:
    assert parse_days(raw, TODAY) is None


def test_parse_deadline() -> None:
    now = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)  # 12:00 по Москве
    assert parse_deadline("11.10 18:00", now, MSK) == "2026-10-11T18:00:00+03:00"
    assert parse_deadline("11.10", now, MSK) == "2026-10-11T23:59:00+03:00"
    assert parse_deadline("02.10 11:00", now, MSK) is None  # сегодня, но уже прошло
    assert parse_deadline("в пятницу", now, MSK) is None
    field = SESSION_SPEC.field("registration_deadline")
    assert parse_field(field, "11.10 18:00", now, MSK) == "2026-10-11T18:00:00+03:00"


@pytest.mark.parametrize(
    ("raw", "kopecks"),
    [("45000", 4_500_000), ("45 000 ₽", 4_500_000), ("80000 руб", 8_000_000), ("0", None), ("сорок", None),
     ("99999999", None)],
)
def test_parse_rubles(raw: str, kopecks: int | None) -> None:
    assert parse_rubles(raw) == kopecks


def test_valid_prices() -> None:
    good = [{"kind": "full", "label": "Полный курс", "amount": 4_500_000},
            {"kind": "group", "label": "Для двух коллег", "amount": 8_000_000}]
    assert valid_prices(good) and valid_prices(None)
    assert not valid_prices([{"kind": "vip", "label": "VIP", "amount": 1}])  # не из 4 категорий
    assert not valid_prices([{"kind": "full", "label": "", "amount": 1}])
    assert not valid_prices([{"kind": "full", "label": "Полный", "amount": 0}])
    assert not valid_prices("Полный курс 45000")


def test_session_fields_by_format() -> None:
    venue, link = SESSION_SPEC.field("venue_id"), SESSION_SPEC.field("online_url")
    assert venue.applies({"format": "offline"}) and not link.applies({"format": "offline"})
    assert link.applies({"format": "online"}) and not venue.applies({"format": "online"})
    assert venue.applies({"format": "hybrid"}) and link.applies({"format": "hybrid"})
    # Курс у проведения и статус не правятся через «✏️» (курс фиксирован, статус — кнопками в карточке).
    assert not SESSION_SPEC.field("program_id").editable and not SESSION_SPEC.field("status").editable


def test_session_card_buttons() -> None:
    markup = admin_views.session_card_markup(SESSION_SPEC, 7, "registration_open", active=True, parent_id=3)
    buttons = _buttons(markup)
    assert "✏️ Дни и время" in buttons and "✏️ Тарифы" in buttons
    assert "✏️ Курс" not in buttons and "✏️ Статус" not in buttons
    assert "✅ 🟢 Идёт набор" in buttons and "🔴 Мест нет" in buttons
    assert texts.BTN_ADMIN_COPY in buttons and texts.BTN_ADMIN_HIDE in buttons
    status_data = next(b.callback_data for row in markup.inline_keyboard for b in row if b.text == "🔴 Мест нет")
    assert status_data.endswith(":full~3")  # статус и курс-родитель для «⬅️ К списку»
    hidden = _buttons(admin_views.session_card_markup(SESSION_SPEC, 7, "full", active=False))
    assert texts.BTN_ADMIN_SHOW in hidden


def test_course_card_has_sessions_and_tbd_toggle() -> None:
    buttons = _buttons(admin_views.course_card_markup(COURSE_SPEC, 5, active=True, has_program=True,
                                                      show_without_dates=True))
    assert texts.BTN_ADMIN_SESSIONS_OF_COURSE in buttons and texts.BTN_ADMIN_NEW_SESSION in buttons
    assert texts.BTN_ADMIN_TBD_HIDE in buttons
    assert texts.BTN_ADMIN_TBD_SHOW in _buttons(admin_views.course_card_markup(
        COURSE_SPEC, 5, active=True, has_program=True, show_without_dates=False))


def test_session_prompts_ref_and_prices() -> None:
    venue = SESSION_SPEC.field("venue_id")
    text, markup = admin_views.field_prompt(SESSION_SPEC, venue, "h", 2, item_id=7,
                                            options=[(1, "Учебный класс"), (2, "Клиника <на Мира>")])
    assert "Клиника &lt;на Мира&gt;" in text  # «Сейчас:» — название площадки, экранированное
    assert _buttons(markup)[:2] == ["Учебный класс", "✅ Клиника <на Мира>"]
    prices = SESSION_SPEC.field("prices")
    assert prices.kind is FieldKind.PRICES
    _, markup = admin_views.field_prompt(SESSION_SPEC, prices, "h", None, selected=["group"])
    assert _buttons(markup) == ["Полный курс", "✅ Для двух коллег", "Только теория", "Практическая часть",
                                texts.BTN_ADMIN_DONE, texts.BTN_ADMIN_SKIP, texts.BTN_ADMIN_CANCEL]
    # Правка: «Сейчас» — сохранённые тарифы (подпись и цена), галочки — категории; есть «Очистить».
    saved = [{"kind": "full", "label": "Полный <курс>", "amount": 4_500_000}]
    text, markup = admin_views.field_prompt(SESSION_SPEC, prices, "h", saved, item_id=7, selected=["full", "theory"])
    assert f"• Полный &lt;курс&gt; — {format_rub(4_500_000)}" in text  # сумма — с неразрывными пробелами
    assert "✅ Только теория" in _buttons(markup) and texts.BTN_ADMIN_CLEAR in _buttons(markup)
    text, markup = admin_views.price_label_prompt(SESSION_SPEC, "h", 1, 2, "group")
    assert "Тариф 1 из 2: Для двух коллег" in text and _buttons(markup)[0] == "✅ «Для двух коллег»"
    text, _ = admin_views.price_amount_prompt(SESSION_SPEC, "h", 2, 2, "Вдвоём <скидка>", "per_group")
    assert "за группу из двух человек" in text and "Вдвоём &lt;скидка&gt;" in text
