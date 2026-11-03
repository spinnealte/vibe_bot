"""Сквозные проверки: настоящий диспетчер бота, настоящая PostgreSQL (временная схема) и подставной Telegram.

Человек (Person) пишет боту и нажимает кнопки - так же, как в приложении: апдейт проходит все middleware, фильтры
и хендлеры, данные пишутся в базу. В конце каждого сценария Harness проверяет:
- Telegram ничего не отклонил (разметка, длина, кнопки) и на каждое нажатие бот ответил ровно один раз;
- в логах нет ошибок, а персональные данные из сценария (телефон, ФИО, e-mail, город...) в лог не попали.
"""

import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from html import unescape
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Contact, Document, Message, PhotoSize, Update, User
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from stubbot.config import Settings
from stubbot.db.enums import DeliveryFormat, PriceKind, PriceUnit, SessionStatus
from stubbot.db.models import Base, CourseSession, Position, PriceOption, Program, SessionDay, Specialty, Venue
from stubbot.payments.fake import FakeGateway
from stubbot.services.legal import publish_documents
from stubbot.tg import texts
from stubbot.tg.dispatcher import create_dispatcher
from stubbot.tg.handlers import (
    admin,
    admin_clients,
    admin_orders,
    admin_pick,
    app_only,
    application,
    cabinet,
    chats,
    consent,
    fallback,
    menu,
    payment,
    registration,
    schedule,
    selection,
    start,
)
from stubbot.tg.handlers.errors import USER_ERROR_TEXT
from stubbot.tg.online_payments import OnlinePayments
from tests.e2e.telegram_stub import BOT_ID, BOT_TOKEN, KNOWN_FILES, FakeTelegram, StoredMessage, new_file_id

OWNER_ID = 900_001
ADMIN_ID = 900_002
MANAGER_URL = "https://t.me/aquastom_manager_e2e"
RUB = 100
TZ = ZoneInfo("Europe/Moscow")

_ROUTER_MODULES = (admin, admin_clients, admin_orders, admin_pick, app_only, application, cabinet, chats, consent,
                   fallback, menu, payment, registration, schedule, selection, start)
HANDLERS_CALLED: set[str] = set()  # «модуль.функция» хендлеров, которые вызвал хоть один сценарий прогона


def all_handlers() -> set[str]:
    """Все хендлеры бота (message, callback_query, my_chat_member) - для отчёта о покрытии сквозными сценариями."""
    names = set()
    for module in _ROUTER_MODULES:
        for observer in (module.router.message, module.router.callback_query, module.router.my_chat_member):
            for handler in observer.handlers:
                names.add(f"{module.__name__.rsplit('.', 1)[-1]}.{handler.callback.__qualname__}")
    return names


def stem(template: str) -> str:
    """Подпись кнопки без подстановок: «💸 Деньги после отмены · {count}» → «💸 Деньги после отмены»."""
    return template.split("{")[0].rstrip(" ·")


def fragment(template: str) -> str:
    """Узнаваемый кусок текста из texts.py, как его видит человек: первая строка до подстановок, без тегов."""
    head = re.sub(r"<[^>]+>", "", template.split("{")[0])
    return unescape(next((line for line in head.split("\n") if line.strip()), head)).strip()


def detach_routers() -> None:
    """Роутеры бота - объекты модулей: в процессе их можно подключить к диспетчеру только один раз. Для нового
    диспетчера (свой на каждый сценарий) отцепляем их от прежнего."""
    for module in _ROUTER_MODULES:
        router = module.router
        parent = router.parent_router
        if parent is not None:
            parent.sub_routers.remove(router)
            router._parent_router = None


def create_engine_in(settings: Settings, schema: str) -> AsyncEngine:
    return create_async_engine(settings.database_url, hide_parameters=True,
                               connect_args={"server_settings": {"search_path": schema}})


class _LogCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@dataclass
class Catalog:
    """Что лежит в афише в начале каждого сценария (даты - от сегодняшнего дня)."""

    program: Program  # «Имплантация» - с программой, двумя проведениями
    main: CourseSession  # набор открыт, 10 мест, два тарифа (один и «для двух коллег»)
    single: PriceOption
    pair: PriceOption
    small: CourseSession  # «Пародонтология»: одно место
    small_price: PriceOption
    no_price: CourseSession  # «Ортодонтия»: цены нет - «стоимость уточнит менеджер»
    soon: CourseSession  # «Гигиена»: начало через 10 дней - отменить самому уже нельзя
    soon_price: PriceOption
    announced: CourseSession  # «Имплантация» без дат: анонс, «Сообщить о наборе»
    specialties: list[Specialty] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)


async def seed_base(session: AsyncSession, now: datetime) -> Catalog:
    today = now.astimezone(TZ).date()
    await publish_documents(session, now)
    for model, titles in ((Specialty, ["Хирургия", "Имплантация", "Ортодонтия"]),
                          (Position, ["Врач", "Ассистент врача", "Главный врач"])):
        await session.execute(insert(model).values(
            [{"title": title, "sort_order": i * 10} for i, title in enumerate(titles, 1)]))
    venue = Venue(name="Учебный класс", address="Санкт-Петербург, Невский пр., 1")
    session.add(venue)

    def program(slug: str, title: str, html: str | None = None) -> Program:
        item = Program(slug=slug, title=title, is_published=True, duration_hours=16, program_html=html)
        session.add(item)
        return item

    def course(item: Program, start: int | None, status: SessionStatus = SessionStatus.REGISTRATION_OPEN,
               capacity: int | None = 10) -> CourseSession:
        dates = {} if start is None else {"start_date": today + timedelta(days=start),
                                          "end_date": today + timedelta(days=start + 1)}
        deadline = None if start is None else datetime.combine(today + timedelta(days=start - 2), time(18), TZ)
        row = CourseSession(program=item, status=status, format=DeliveryFormat.OFFLINE, venue=venue,
                            capacity=capacity, registration_deadline=deadline, **dates)
        session.add(row)
        if start is not None:
            for offset in (0, 1):
                session.add(SessionDay(session=row, date=today + timedelta(days=start + offset),
                                       start_time=time(10), end_time=time(18)))
        return row

    def price(row: CourseSession, label: str, rubles: int, pair: bool = False, order: int = 10) -> PriceOption:
        option = PriceOption(session=row, kind=PriceKind.GROUP if pair else PriceKind.FULL, label=label,
                             amount=rubles * RUB, unit=PriceUnit.PER_GROUP if pair else PriceUnit.PER_PERSON,
                             min_seats=2 if pair else 1, max_seats=2 if pair else None, sort_order=order)
        session.add(option)
        return option

    implants = program("e2e-implants", "Имплантация", "<b>День 1</b>\n• Теория\n\n<b>День 2</b>\n• Практика")
    main = course(implants, 30)
    single = price(main, "Один участник", 45_000)
    pair = price(main, "Для двух коллег", 80_000, pair=True, order=20)
    perio = program("e2e-perio", "Пародонтология")
    small = course(perio, 35, capacity=1)
    small_price = price(small, "Участие", 30_000)
    ortho = program("e2e-ortho", "Ортодонтия")
    no_price = course(ortho, 40)
    hygiene = program("e2e-hygiene", "Гигиена")
    soon = course(hygiene, 10)
    soon_price = price(soon, "Участие", 20_000)
    announced = course(implants, None, status=SessionStatus.ANNOUNCED)
    await session.flush()
    return Catalog(implants, main, single, pair, small, small_price, no_price, soon, soon_price, announced,
                   list(await session.scalars(select(Specialty).order_by(Specialty.id))),
                   list(await session.scalars(select(Position).order_by(Position.id))))


class Harness:
    """Один сценарий: бот, база и люди в чатах."""

    def __init__(self, settings: Settings, schema: str) -> None:
        self.settings = settings
        self.schema = schema
        self.tg = FakeTelegram()
        self.bot = Bot(BOT_TOKEN, session=self.tg, default=DefaultBotProperties(parse_mode=ParseMode.HTML,
                                                                                 link_preview_is_disabled=True))
        self.engine = create_engine_in(settings, schema)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.gateway = FakeGateway()
        self.payments = OnlinePayments(self.bot, self.factory, settings, self.gateway)
        self.storage = MemoryStorage()
        detach_routers()
        self.dp: Dispatcher = create_dispatcher(settings, self.factory, storage=self.storage,
                                                online_payments=self.payments)
        self.catalog: Catalog | None = None
        self.pii: set[str] = set()  # что не должно попасть в логи
        self.allowed_errors: list[str] = []  # подстроки ERROR-записей, которых сценарий ждёт
        self._update_ids = iter(range(1, 10**9))
        self._logs = _LogCollector()

    async def __aenter__(self) -> "Harness":
        logging.getLogger().addHandler(self._logs)
        self._old_level = logging.getLogger("stubbot").level
        logging.getLogger("stubbot").setLevel(logging.DEBUG)
        async with self.engine.begin() as conn:
            tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
            await conn.execute(text("ALTER SEQUENCE order_number_seq RESTART WITH 100001"))
        async with self.factory() as session, session.begin():
            self.catalog = await seed_base(session, datetime.now(UTC))
        return self

    async def __aexit__(self, *exc: object) -> None:
        for record in self.logs:
            if record.name == "stubbot.handlers" and isinstance(record.args, tuple) and len(record.args) == 3:
                HANDLERS_CALLED.add(record.args[1])
        if self.tg.left_chats:  # my_chat_member в журнал хендлеров не пишется - выход из группы виден по запросу
            HANDLERS_CALLED.add("chats.leave_non_private_chat")
        logging.getLogger().removeHandler(self._logs)
        logging.getLogger("stubbot").setLevel(self._old_level)
        detach_routers()  # роутеры свободны для диспетчера следующего теста (в том числе tests/test_security.py)
        await self.engine.dispose()
        if exc[0] is None:
            self.check()

    # --- Итоговые проверки ---------------------------------------------------------------------------------------

    @property
    def logs(self) -> list[logging.LogRecord]:
        return self._logs.records

    def log_text(self) -> str:
        return "\n".join(f"{r.levelname} {r.name}: {r.getMessage()}" for r in self.logs)

    def check(self) -> None:
        assert not self.tg.violations, "Telegram отклонил бы:\n" + "\n".join(self.tg.violations)
        errors = [r for r in self.logs if r.levelno >= logging.ERROR
                  and not any(allowed in r.getMessage() for allowed in self.allowed_errors)]
        assert not errors, "Ошибки в логе:\n" + "\n".join(
            f"{r.name}: {r.getMessage()}\n{logging.Formatter().formatException(r.exc_info) if r.exc_info else ''}"
            for r in errors)
        leaked = {value for value in self.pii if value and any(value in r.getMessage() for r in self.logs)}
        assert not leaked, f"Персональные данные в логе: {leaked}"

    # --- Люди и время --------------------------------------------------------------------------------------------

    def person(self, user_id: int, first_name: str = "Тест", username: str | None = None,
               last_name: str | None = None) -> "Person":
        return Person(self, user_id, first_name, username, last_name)

    def owner(self) -> "Person":
        return self.person(OWNER_ID, "Владелец", "owner_e2e")

    def admin(self) -> "Person":
        return self.person(ADMIN_ID, "Админ", "admin_e2e")

    def next_update_id(self) -> int:
        return next(self._update_ids)

    async def feed(self, update: Update) -> None:
        await self.dp.feed_update(self.bot, update)

    def state(self, user_id: int) -> FSMContext:
        return FSMContext(self.storage, StorageKey(bot_id=BOT_ID, chat_id=user_id, user_id=user_id))

    async def db(self, query, *args):
        """Короткий запрос к базе в своей транзакции (проверить, что записалось)."""
        async with self.factory() as session:
            return await query(session, *args)

    async def scalar(self, stmt):
        async with self.factory() as session:
            return await session.scalar(stmt)

    async def scalars(self, stmt) -> list:
        async with self.factory() as session:
            return list(await session.scalars(stmt))


class Person:
    """Человек в личном чате с ботом."""

    def __init__(self, h: Harness, user_id: int, first_name: str, username: str | None, last_name: str | None) -> None:
        self.h = h
        self.id = user_id
        self.user = User(id=user_id, is_bot=False, first_name=first_name, last_name=last_name, username=username,
                         language_code="ru")
        self.chat = Chat(id=user_id, type="private", first_name=first_name)
        self.last_answer = None  # ответ бота на последнее нажатие (AnswerCallbackQuery)

    # --- Что видно в чате ----------------------------------------------------------------------------------------

    @property
    def tg(self) -> FakeTelegram:
        return self.h.tg

    def messages(self) -> list[StoredMessage]:
        return self.tg.messages(self.id)

    @property
    def last(self) -> StoredMessage:
        return self.messages()[-1]

    def mark(self) -> int:
        """Сколько сообщений бота уже есть: после действия - только новые (since)."""
        return len(self.tg.chats.get(self.id, []))

    def since(self, mark: int) -> list[StoredMessage]:
        return [m for m in self.tg.chats.get(self.id, [])[mark:] if m.from_bot and not m.deleted]

    def texts(self) -> list[str]:
        return [m.plain or "" for m in self.messages()]

    def sees(self, part: str) -> bool:
        """Есть ли в чате сообщение бота с этим текстом (кусок текста или шаблон из texts.py)."""
        part = fragment(part) if ("<" in part or "{" in part) else part
        return any(part in t for t in self.texts())

    def last_text(self) -> str:
        return self.last.plain or ""

    def reply_keyboard(self) -> list[str]:
        """Кнопки нижнего меню, которые сейчас у человека (последняя присланная reply-клавиатура; после «убрать
        клавиатуру» - никаких)."""
        from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove

        for message in reversed(self.tg.chats.get(self.id, [])):
            if message.from_bot and isinstance(message.markup, ReplyKeyboardRemove):
                return []
            if message.from_bot and isinstance(message.markup, ReplyKeyboardMarkup):
                return [b.text for row in message.markup.keyboard for b in row]
        return []

    def find_button(self, label: str, message: StoredMessage | None = None):
        """Кнопка с этим текстом в самом свежем сообщении, где она есть (точное совпадение, иначе - вхождение)."""
        candidates = [message] if message is not None else list(reversed(self.messages()))
        for exact in (True, False):
            for candidate in candidates:
                for button in candidate.buttons():
                    if (button.text == label) if exact else (label in button.text):
                        return candidate, button
        shown = [[b.text for b in m.buttons()] for m in candidates[:3]]
        raise AssertionError(f"Кнопки {label!r} нет. Последние кнопки: {shown}")

    def has_button(self, label: str, message: StoredMessage | None = None) -> bool:
        try:
            self.find_button(label, message)
        except AssertionError:
            return False
        return True

    # --- Действия ------------------------------------------------------------------------------------------------

    def _message(self, **fields) -> Message:
        stored = self.tg.add_user_message(self.id, html=fields.get("text"), plain=fields.get("text"))
        return Message(message_id=stored.message_id, date=datetime.now(UTC), chat=self.chat, from_user=self.user,
                       **fields)

    async def send(self, text: str) -> None:
        self.tg.started.add(self.id)
        self.tg.blocked.discard(self.id)
        await self.h.feed(Update(update_id=self.h.next_update_id(), message=self._message(text=text)))

    async def start(self, payload: str | None = None) -> None:
        await self.send("/start" + (f" {payload}" if payload else ""))

    async def share_contact(self, phone: str, own: bool = True) -> None:
        contact = Contact(phone_number=phone, first_name=self.user.first_name,
                          user_id=self.id if own else self.id + 1)
        self.tg.started.add(self.id)
        await self.h.feed(Update(update_id=self.h.next_update_id(), message=self._message(contact=contact)))

    async def send_document(self, file_name: str, content: bytes = b"%PDF-1.4 e2e", caption: str | None = None,
                            mime: str = "application/pdf") -> str:
        file_id = new_file_id("doc")
        KNOWN_FILES[file_id] = content
        document = Document(file_id=file_id, file_unique_id=file_id, file_name=file_name, mime_type=mime,
                            file_size=len(content))
        await self.h.feed(Update(update_id=self.h.next_update_id(),
                                 message=self._message(document=document, caption=caption)))
        return file_id

    async def send_photo(self, caption: str | None = None) -> str:
        file_id = new_file_id("photo")
        KNOWN_FILES[file_id] = b"\x89PNG e2e"
        photo = [PhotoSize(file_id=file_id, file_unique_id=file_id, width=800, height=600)]
        await self.h.feed(Update(update_id=self.h.next_update_id(), message=self._message(photo=photo, caption=caption)))
        return file_id

    async def send_sticker_like(self) -> None:
        """Что-то, что не текст и не файл (стикер, голосовое) - у Message тогда нет ни text, ни document."""
        from aiogram.types import Sticker

        sticker = Sticker(file_id="sticker-1", file_unique_id="sticker-1", type="regular", width=512, height=512,
                          is_animated=False, is_video=False)
        await self.h.feed(Update(update_id=self.h.next_update_id(), message=self._message(sticker=sticker)))

    async def click(self, label: str, message: StoredMessage | None = None):
        """Нажать inline-кнопку с этим текстом. Возвращает ответ бота на нажатие (всплывающее окно или None)."""
        stored, button = self.find_button(label, message)
        if button.callback_data is None:
            raise AssertionError(f"Кнопка {label!r} - ссылка ({button.url}), её открывает Telegram, а не бот")
        return await self.press(button.callback_data, stored)

    async def press(self, data: str, message: StoredMessage | None = None):
        """Нажатие с такими данными - в том числе подделанными (кнопки с ними бот не присылал)."""
        if message is None:
            with_buttons = [m for m in self.messages() if m.buttons()]
            message = with_buttons[-1] if with_buttons else self.last
        query_id = secrets.token_hex(6)
        query = CallbackQuery(id=query_id, from_user=self.user, chat_instance=f"ci-{self.id}",
                              message=message.to_message(self.h.bot), data=data)
        await self.h.feed(Update(update_id=self.h.next_update_id(), callback_query=query))
        answer = self.tg.answers.get(query_id)
        if answer is None:
            self.tg.violations.append(f"нажатие {data!r} осталось без ответа - у человека крутятся «часики»")
        self.last_answer = answer
        return answer

    def alert(self) -> str | None:
        """Текст всплывающего окна последнего нажатия."""
        return self.last_answer.text if self.last_answer is not None else None

    def got_error(self) -> bool:
        return self.alert() == USER_ERROR_TEXT or self.sees(USER_ERROR_TEXT)

    # --- Сценарии целиком ----------------------------------------------------------------------------------------

    async def register(self, phone: str, full_name: str, ads: bool = False, optional: dict[str, str] | None = None,
                       specialties: int = 1, positions: int = 1) -> None:
        """/start → «Личный кабинет» → согласие → телефон → ФИО → специальности → должности → необязательные
        поля (optional - ответы текстом по порядку, остальное «Пропустить»)."""
        self.h.pii.update({phone, phone.lstrip("+"), full_name})
        self.h.pii.update(v for v in (optional or {}).values() if len(v) > 3)
        await self.start()
        await self.send(texts.BTN_CABINET)
        await self.click(texts.CONSENT_FORM_PD)
        if ads:
            await self.click(texts.CONSENT_FORM_ADS)
        await self.click(texts.CONSENT_FORM_CONTINUE)
        await self.share_contact(phone)
        await self.send(full_name)
        await self.click(texts.REG_NAME_OK)
        catalog = self.h.catalog
        for item in catalog.specialties[:specialties]:
            await self.click(item.title)
        await self.click(texts.REG_DONE_BUTTON)
        for item in catalog.positions[:positions]:
            await self.click(item.title)
        await self.click(texts.REG_DONE_BUTTON)
        answers = list((optional or {}).values())
        for index in range(5):
            if index < len(answers) and answers[index]:
                await self.send(answers[index])
            else:
                await self.click(texts.BTN_SKIP)
        assert self.sees(texts.REG_COMPLETED), self.texts()[-3:]

    async def open_schedule(self) -> None:
        await self.send(texts.BTN_SCHEDULE)

    async def go_to_course(self, title: str, limit: int = 10) -> None:
        """Листать афишу ▶️, пока не покажется курс с этим названием."""
        for _ in range(limit):
            if title in (self.last.plain or ""):
                return
            await self.click(texts.BTN_NEXT_PAGE)
        raise AssertionError(f"Курса {title!r} нет в афише")

    async def enroll(self, title: str, price_label: str | None = None) -> None:
        """Из афиши: курс → «Записаться» → тариф (если их несколько) → «✅ Записаться»."""
        await self.open_schedule()
        await self.go_to_course(title)
        await self.click(texts.BTN_APPLY)
        if price_label is not None:
            await self.click(price_label)
        await self.click(texts.BTN_APPLY_SEND)


async def crawl(person: Person, max_clicks: int = 250, skip: Callable[[str, str], bool] = lambda text, data: False,
                after_click: Callable[[Person], Awaitable[None]] | None = None,
                restart: Callable[[Person], Awaitable[None]] | None = None) -> list[str]:
    """Обойти все кнопки, до которых человек может дойти: нажимать каждую ещё не нажатую кнопку из сообщений
    в чате (как они выглядят сейчас), пока новых не останется. Экраны меняют то же сообщение, поэтому кнопки
    прежних экранов пропадают - тогда restart заново открывает вход (меню), и обход идёт дальше.
    Возвращает нажатые callback_data."""
    clicked: list[str] = []
    seen: set[str] = set()
    stuck = False
    while len(clicked) < max_clicks:
        target = None
        for message in reversed(person.messages()):
            for button in message.buttons():
                data = button.callback_data
                if data is None or data in seen or skip(button.text, data):
                    continue
                target = (message, data)
                break
            if target:
                break
        if target is None:
            if restart is None or stuck:
                break
            stuck = True
            await restart(person)
            continue
        stuck = False
        message, data = target
        seen.add(data)
        clicked.append(data)
        await person.press(data, message)
        if after_click is not None:
            await after_click(person)
    return clicked
