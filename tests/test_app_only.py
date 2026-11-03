"""Всё в приложении (WEBAPP_ONLY=true): настройка, кнопки приложения, напоминание и уведомления админам.
Без БД и Telegram. Сам бот в этом режиме - tests/e2e/test_e2e_app_only.py."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from stubbot.config import Settings, get_settings
from stubbot.services.reminders import ReminderKind
from stubbot.tg import admin_orders_views, keyboards, texts
from stubbot.tg.reminders import letters
from tests.test_reminders import TZ, _job, _rows, _settings

URL = "https://app.example.test"


def _config(**overrides: object) -> Settings:
    values = {"db_user": "u", "db_password": "p", "db_name": "n", "owner_telegram_id": 1, "webapp_url": URL,
              "webapp_only": True, **overrides}
    return Settings(_env_file=None, **values)


def test_app_only_needs_app_address() -> None:
    with pytest.raises(ValidationError, match="WEBAPP_URL"):
        _config(webapp_url=None)
    assert _config().webapp_only and not _config(webapp_only=False).webapp_only
    assert not Settings(_env_file=None, db_user="u", db_password="p", db_name="n", owner_telegram_id=1).webapp_only


def test_app_button_opens_screen_by_parameter() -> None:
    """Экран - параметром go (после «#» Telegram дописывает подпись пользователя), кнопка - только web_app."""
    assert keyboards.app_page(URL) == URL
    assert keyboards.app_page(URL, "/my?order=5") == f"{URL}/?go=/my%3Forder%3D5"
    button = keyboards.app_button(URL, texts.BTN_APP_RECORD, "/admin/order/7")
    assert button.web_app.url == f"{URL}/?go=/admin/order/7"
    assert button.callback_data is None and button.url is None
    assert keyboards.open_webapp(URL).inline_keyboard[0][0].web_app.url == URL


def test_app_link_only_when_everything_is_in_app() -> None:
    markup = keyboards.app_link(_config(), texts.BTN_APP_MY, "/my")
    assert _rows(markup) == [[texts.BTN_APP_MY]] and markup.inline_keyboard[0][0].web_app.url == f"{URL}/?go=/my"
    assert keyboards.app_link(_config(webapp_only=False), texts.BTN_APP_MY, "/my") is None


def test_app_hint_with_manager_only_for_questions() -> None:
    assert _rows(keyboards.app_hint(URL, None)) == [[texts.BTN_WEBAPP]]
    markup = keyboards.app_hint(URL, "https://t.me/manager?text=x")
    assert _rows(markup) == [[texts.BTN_WEBAPP], [texts.BTN_MANAGER]]
    assert markup.inline_keyboard[1][0].url == "https://t.me/manager?text=x"


def test_payment_reminder_opens_record_in_app() -> None:
    """Вместо «Физлицо / Юрлицо» и «Оплатить онлайн» - запись в приложении (там и выбор, и оплата); менеджер - как был."""
    app = SimpleNamespace(**{**vars(_settings(online=True)), "webapp_only": True, "webapp_url": URL})
    client = letters(_job(), app)[0]
    assert _rows(client.markup) == [[texts.BTN_APP_RECORD], [texts.BTN_CANCEL_VIA_MANAGER]]
    assert client.markup.inline_keyboard[0][0].web_app.url == f"{URL}/?go=/my%3Forder%3D77"
    overdue = letters(_job(ReminderKind.OVERDUE), SimpleNamespace(**{**vars(app), "manager_url": None}))[0]
    assert _rows(overdue.markup) == [[texts.BTN_APP_RECORD]]


def test_admin_notifications_open_order_in_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBAPP_ONLY", "true")
    monkeypatch.setenv("WEBAPP_URL", URL)
    get_settings.cache_clear()
    button = admin_orders_views.open_order(77).inline_keyboard[0][0]
    assert button.text == texts.BTN_ADMIN_OPEN_ORDER and button.web_app.url == f"{URL}/?go=/admin/order/77"
    # Просрочка: «Снять бронь» - в карточке заказа в приложении, под уведомлением - только она.
    _, markup = admin_orders_views.reminder_notice(_job(ReminderKind.OVERDUE), TZ)
    assert _rows(markup) == [[texts.BTN_ADMIN_OPEN_ORDER]] and markup.inline_keyboard[0][0].web_app is not None
