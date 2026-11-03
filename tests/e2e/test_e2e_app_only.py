"""Всё в приложении (WEBAPP_ONLY=true): бот - вход в приложение. После /start - одно сообщение с кнопкой приложения,
нижнего меню нет; на сообщения, старые кнопки и старое меню - подсказка, где теперь всё."""

import pytest
from aiogram.types import ReplyKeyboardRemove
from sqlalchemy import select

from stubbot.config import get_settings
from stubbot.db.models import Client
from stubbot.tg import texts
from tests.e2e.harness import MANAGER_URL, Harness, fragment

APP_URL = "https://app.example.test"


@pytest.fixture
def app_only(e2e, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WEBAPP_ONLY", "true")
    monkeypatch.setenv("WEBAPP_URL", APP_URL)
    get_settings.cache_clear()
    return e2e


def _only_app_button(message) -> bool:
    buttons = message.buttons()
    return len(buttons) == 1 and buttons[0].text == texts.BTN_WEBAPP and buttons[0].web_app.url == APP_URL


def test_start_is_one_welcome_with_app_button(app_only) -> None:
    async def scenario(h: Harness) -> None:
        anna = h.person(7001, "Анна")
        mark = anna.mark()
        await anna.start("src_site")
        [welcome] = anna.since(mark)
        assert fragment(texts.WELCOME_APP_NEW) in welcome.plain and "слева от поля ввода" in welcome.plain
        assert _only_app_button(welcome) and anna.reply_keyboard() == []
        # Откуда пришёл - записано, как и раньше (хвост ссылки /start).
        assert await h.scalar(select(Client.start_param).where(Client.telegram_id == 7001)) == "src_site"

        mark = anna.mark()
        await anna.start()
        [again] = anna.since(mark)
        assert fragment(texts.WELCOME_APP_BACK) in again.plain and _only_app_button(again)

    app_only.run(scenario)


def test_messages_and_old_buttons_lead_to_app(app_only) -> None:
    async def scenario(h: Harness) -> None:
        anna, admin = h.person(7002, "Анна"), h.admin()
        await anna.start()

        # Вопрос текстом - приложение и менеджер с этим вопросом в черновике, одним сообщением.
        mark = anna.mark()
        await anna.send("Сколько стоит курс?")
        [hint] = anna.since(mark)
        assert fragment(texts.APP_HINT) in hint.plain and fragment(texts.APP_HINT_MANAGER) in hint.plain
        app, manager = hint.buttons()
        assert app.web_app.url == APP_URL and manager.url.startswith(MANAGER_URL + "?text=")

        # Команда или стикер - не вопрос: только приложение.
        for action in (anna.send("/help"), anna.send_sticker_like()):
            mark = anna.mark()
            await action
            [hint] = anna.since(mark)
            assert fragment(texts.APP_HINT_MANAGER) not in hint.plain and _only_app_button(hint)

        # Кнопка старого нижнего меню - меню убирается, вход - кнопкой «Приложение» у поля ввода.
        for label in (texts.BTN_SCHEDULE, texts.BTN_ADMIN):
            mark = anna.mark()
            await anna.send(label)
            [moved] = anna.since(mark)
            assert fragment(texts.APP_MENU_MOVED) in moved.plain and isinstance(moved.markup, ReplyKeyboardRemove)
        assert anna.reply_keyboard() == []
        await admin.send(texts.BTN_ADMIN)
        assert fragment(texts.APP_ADMIN_MOVED) in admin.last_text()

        # Кнопка из старого сообщения бота - всплывающее окно, без новых сообщений.
        mark = anna.mark()
        await anna.press("sch:old:0:0:0")
        assert anna.alert() == texts.APP_OLD_BUTTON and anna.since(mark) == []

        # Номер, которым поделились в приложении, приходит в чат контактом - бот молчит.
        await anna.share_contact("+79990007002")
        assert anna.since(mark) == []

    app_only.run(scenario)
