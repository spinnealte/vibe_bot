"""Защита данных без БД и Telegram: только личные чаты, ограничение частоты, срок жизни шагов в Redis,
журнал действий админа."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import LeaveChat
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    Update,
    User,
)

from stubbot.config import Settings
from stubbot.logging_setup import setup_logging
from stubbot.services.admin_log import ADMIN_LOGGER_NAME, log_admin_action
from stubbot.tg import texts
from stubbot.tg.callbacks import AdminAction, AdminCb
from stubbot.tg.dispatcher import FSM_TTL, create_dispatcher, create_storage
from stubbot.tg.handlers import chats
from stubbot.tg.middlewares import ClientMiddleware, ThrottlingMiddleware

OWNER_ID = 1
GROUP = Chat(id=-1001234, type="supergroup")
NOW = datetime(2026, 10, 5, 12, 0)


class _NoTelegram(BaseSession):
    """Вместо Telegram: запоминает, что бот пытался отправить."""

    def __init__(self) -> None:
        super().__init__()
        self.methods: list = []

    async def make_request(self, bot, method, timeout=None):
        self.methods.append(method)
        return True

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError

    async def close(self) -> None:
        pass


class _NoDb:
    """Фабрика сессий БД, которая не даёт к БД обратиться: тест упадёт, если бот туда пойдёт."""

    def __call__(self) -> "_NoDb":
        return self

    def begin(self) -> "_NoDb":
        return self

    async def __aenter__(self) -> "_NoDb":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __getattr__(self, name: str):
        raise AssertionError(f"обращение к БД: {name}")


@pytest.fixture(scope="module")
def dp() -> Dispatcher:
    # Диспетчер в процессе один: роутеры — объекты модулей и подключаются к корневому один раз.
    settings = Settings(_env_file=None, db_user="u", db_password="p", db_name="n", owner_telegram_id=OWNER_ID)
    return create_dispatcher(settings, _NoDb(), storage=MemoryStorage())


def _bot() -> Bot:
    return Bot("123456:" + "A" * 35, session=_NoTelegram())


def test_bot_is_silent_in_groups_even_for_admin(dp: Dispatcher) -> None:
    """В группе бот не отвечает никому: ни кабинета, ни админки, ни выгрузки на виду у участников. БД не трогает."""
    bot, owner = _bot(), User(id=OWNER_ID, is_bot=False, first_name="Владелец")
    in_group = lambda **kw: Message(message_id=1, date=NOW, chat=GROUP, from_user=owner, **kw)  # noqa: E731
    export = CallbackQuery(id="1", from_user=owner, chat_instance="ci", message=in_group(text="меню"),
                           data=AdminCb(action=AdminAction.EXPORT).pack())

    async def scenario() -> None:
        for number, text in enumerate(("/start", texts.BTN_CABINET, texts.BTN_ADMIN), start=1):
            await dp.feed_update(bot, Update(update_id=number, message=in_group(text=text)))
        await dp.feed_update(bot, Update(update_id=9, callback_query=export))

    asyncio.run(scenario())
    assert bot.session.methods == []


def test_bot_leaves_group_when_added(dp: Dispatcher) -> None:
    bot, me = _bot(), User(id=123456, is_bot=True, first_name="Бот")
    added = ChatMemberUpdated(chat=GROUP, from_user=User(id=77, is_bot=False, first_name="Т"), date=NOW,
                              old_chat_member=ChatMemberLeft(user=me), new_chat_member=ChatMemberMember(user=me))
    asyncio.run(dp.feed_update(bot, Update(update_id=20, my_chat_member=added)))
    assert [(type(m), m.chat_id) for m in bot.session.methods] == [(LeaveChat, GROUP.id)]


def test_leave_handler_ignores_removal() -> None:
    """Бота убрали из группы — выходить уже неоткуда."""
    left: list[int] = []

    class _FakeBot:
        async def leave_chat(self, chat_id: int) -> None:
            left.append(chat_id)

    event = SimpleNamespace(chat=SimpleNamespace(id=-5, type=ChatType.GROUP),
                            new_chat_member=SimpleNamespace(status=ChatMemberStatus.LEFT))
    asyncio.run(chats.leave_non_private_chat(event, _FakeBot()))
    assert left == []


def test_client_is_not_created_in_group() -> None:
    data = {"event_from_user": User(id=77, is_bot=False, first_name="Т"), "event_chat": GROUP, "session": None}

    async def handler(event: object, data: dict) -> str:
        return "ok"

    assert asyncio.run(ClientMiddleware()(handler, None, data)) == "ok"
    assert "client" not in data  # с session=None обращение к БД упало бы


def test_throttle_registered_for_messages_and_buttons(dp: Dispatcher) -> None:
    for observer in (dp.message, dp.callback_query):
        first = observer.outer_middleware[0]
        assert isinstance(first, ThrottlingMiddleware)  # раньше остальных — до работы с БД
        assert (first.limit, first.window) == (20, 5.0)
    assert dp.message.outer_middleware[0] is dp.callback_query.outer_middleware[0]  # счёт общий


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Msg(Message):
    async def answer(self, text: str, **kwargs: object) -> None:
        _WARNINGS.append(("message", text))


class _Btn(CallbackQuery):
    async def answer(self, text: str | None = None, show_alert: bool | None = None, **kwargs: object) -> None:
        _WARNINGS.append(("alert" if show_alert else "toast", text))


_WARNINGS: list[tuple[str, str | None]] = []


def _hit(middleware: ThrottlingMiddleware, user_id: int = 5, button: bool = False, chat_type: str = "private"):
    user = User(id=user_id, is_bot=False, first_name="Т")
    event = (_Btn(id="1", from_user=user, chat_instance="ci") if button
             else _Msg(message_id=1, date=NOW, chat=Chat(id=user_id, type="private")))

    async def handler(event: object, data: dict) -> str:
        return "ok"

    data = {"event_from_user": user, "event_chat": Chat(id=user_id, type=chat_type)}
    return asyncio.run(middleware(handler, event, data))


def test_throttle_twenty_per_five_seconds() -> None:
    _WARNINGS.clear()
    clock = _Clock()
    middleware = ThrottlingMiddleware(limit=20, window=5.0, clock=clock)
    for _ in range(20):  # сообщения и кнопки считаются вместе
        clock.now += 0.1
        assert _hit(middleware, button=bool(_ % 2)) == "ok"
    assert _hit(middleware) is None and _WARNINGS == [("message", texts.FLOOD_WAIT)]
    assert _hit(middleware, button=True) is None and _hit(middleware) is None
    assert len(_WARNINGS) == 1  # просьба подождать — один раз за окно, а не на каждое лишнее обращение
    assert _hit(middleware, user_id=6) == "ok"  # другого человека это не касается

    clock.now += 3.0  # часть окна прошла, но первые обращения ещё в нём
    assert _hit(middleware) is None
    clock.now += 2.1  # с первого обращения прошло больше 5 секунд — место освободилось
    assert _hit(middleware) == "ok"

    clock.now += 10.0  # человек успокоился и снова разогнался: предупреждение приходит заново, теперь на кнопку
    for _ in range(20):
        assert _hit(middleware, button=True) == "ok"
    assert _hit(middleware, button=True) is None
    assert _WARNINGS[-1] == ("alert", texts.FLOOD_WAIT) and len(_WARNINGS) == 2


def test_throttle_skips_groups_and_forgets_idle_users() -> None:
    clock = _Clock()
    middleware = ThrottlingMiddleware(limit=2, window=5.0, clock=clock)
    assert all(_hit(middleware, chat_type="supergroup") == "ok" for _ in range(10))  # в группе бот и так молчит
    assert _hit(middleware, user_id=8) == "ok"
    clock.now += 61.0
    assert _hit(middleware, user_id=9) == "ok"
    assert set(middleware._hits) == {9}  # молчавший минуту из памяти убран


def test_unfinished_scenarios_expire_in_redis() -> None:
    storage = create_storage("redis://127.0.0.1:6379/0")  # к Redis не подключается, пока к нему не обратились
    assert storage.state_ttl == FSM_TTL and storage.data_ttl == FSM_TTL
    assert FSM_TTL.days == 2
    asyncio.run(storage.close())


def test_admin_actions_go_to_separate_file(tmp_path: Path) -> None:
    setup_logging("WARNING", tmp_path)
    loggers = (logging.getLogger(), logging.getLogger(ADMIN_LOGGER_NAME))
    try:
        log_admin_action(123456789, "курс", "#%s: изменено поле '%s'", 12, "title")
        logging.getLogger("stubbot.handlers").info("обычная строка лога")
        for logger in loggers:
            for handler in logger.handlers:
                handler.flush()
        admin_log = (tmp_path / "admin.log").read_text(encoding="utf-8")
        # Когда (время в начале строки), кто (Telegram ID) и что сделал.
        assert admin_log.count("\n") == 1 and admin_log[:4].isdigit()
        assert " | INFO | stubbot.admin | Админ 123456789 · курс · #12: изменено поле 'title'" in admin_log
        assert "обычная строка" not in admin_log  # в файле только действия админа
    finally:
        for logger in loggers:
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
