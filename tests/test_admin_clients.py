"""Клиенты в админке (полная версия, этап 1, шаг 4.2): поиск, карточка клиента, участники проведения, листы
ожидания курсов, правка аванса. Без БД и Telegram."""

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest

from stubbot.db.enums import AdvanceReason, EnrollmentStatus, OrderStatus
from stubbot.repositories.admin_clients import like_escape
from stubbot.services.admin_clients import (
    ADVANCE_REASON_LIMIT,
    SEARCH_LIMIT,
    AdminClientCard,
    AdminClientService,
    AdvanceInput,
    AdvanceInputError,
    AdvanceOutcome,
    QueryKind,
    SearchQuery,
    SearchResult,
    SessionParticipants,
    parse_advance,
    parse_query,
)
from stubbot.services.admin_dictionaries import DictKind
from stubbot.tg import admin_clients_views as views
from stubbot.tg import admin_orders_views, admin_views, texts
from stubbot.tg.callbacks import ClientAdminAction, ClientAdminCb, DictAction, DictCb, OrderAdminAction, OrderAdminCb
from stubbot.tg.outgoing import Delivery, Outgoing, OutgoingKind, deliver_to_client
from stubbot.utils.money import format_rub

TZ = "Europe/Moscow"
CREATED = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
PETROV = SimpleNamespace(id=10, full_name="Петров Пётр Петрович", phone="+79990000010", telegram_id=111,
                         tg_username="petrov", is_bot_blocked=False, email="petrov@mail.ru", city="Санкт-Петербург",
                         workplace_raw="Клиника «Улыбка»", specialties=[SimpleNamespace(title="Хирургия")],
                         created_at=CREATED)
COLLEAGUE = SimpleNamespace(id=50, full_name="Петрова Анна Сергеевна", phone="+79991234567", telegram_id=None,
                            tg_username=None, is_bot_blocked=False, email=None, city=None, workplace_raw=None,
                            specialties=[], created_at=CREATED)


def _rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def _unpack(button) -> ClientAdminCb:
    return ClientAdminCb.unpack(button.callback_data)


# --- Разбор ввода --------------------------------------------------------------------------------------------------

def test_query_by_name_or_phone() -> None:
    assert parse_query("петр") == SearchQuery(QueryKind.NAME, words=("петр",))
    assert parse_query("  Пётр   ИВАНОВ ") == SearchQuery(QueryKind.NAME, words=("петр", "иванов"))  # ё = е
    assert parse_query("4567") == SearchQuery(QueryKind.PHONE, digits="4567")
    assert parse_query("+7 999 123-45-67") == SearchQuery(QueryKind.PHONE, digits="79991234567")
    for short in ("45", "п", "   ", "-", "№1"):  # от 2 букв или от 4 цифр - иначе выгрузили бы всех
        assert parse_query(short).kind is QueryKind.TOO_SHORT
    assert len("".join(parse_query("я" * 500).words)) == 100  # длинный запрос обрезается


def test_like_wildcards_are_plain_characters() -> None:
    assert like_escape("50%_x\\") == "50\\%\\_x\\\\"


def test_advance_input() -> None:
    assert parse_advance("+5000 подарок за отзыв") == AdvanceInput(500_000, "подарок за отзыв")
    assert parse_advance("-5 000 ₽ ошибка") == AdvanceInput(-500_000, "ошибка")
    assert parse_advance("+5000р  возврат   части ") == AdvanceInput(500_000, "возврат части")
    assert parse_advance("+50 000 подарок") == AdvanceInput(5_000_000, "подарок")
    assert parse_advance("+5000 100 причина") == AdvanceInput(500_000, "100 причина")  # не группа тысяч
    assert parse_advance("+5000") is AdvanceInputError.NO_REASON
    assert parse_advance("5000 без знака") is AdvanceInputError.INVALID
    assert parse_advance("+0 ноль") is AdvanceInputError.INVALID
    assert parse_advance("+5000 " + "я" * (ADVANCE_REASON_LIMIT + 1)) is AdvanceInputError.TOO_LONG


# --- Сервис --------------------------------------------------------------------------------------------------------

class _Repo:
    def __init__(self, found=(), client=None, rows=()) -> None:
        self.found = list(found)
        self.client = client
        self.rows = list(rows)
        self.calls: list = []

    async def search_by_name(self, words, limit):
        self.calls.append(("name", words, limit))
        return self.found[:limit]

    async def search_by_phone(self, digits, limit):
        self.calls.append(("phone", digits, limit))
        return self.found[:limit]

    async def client_with_profile(self, client_id):
        return self.client if self.client is not None and self.client.id == client_id else None

    async def session_for_participants(self, session_id):
        return SimpleNamespace(id=session_id, capacity=10)

    async def participants(self, session_id):
        return self.rows


class _Advances:
    def __init__(self, balance: int = 0) -> None:
        self.start, self.added, self.locked = balance, [], []

    async def balance(self, client_id: int) -> int:
        return self.start + sum(a.amount for a in self.added)

    async def lock_client(self, client_id: int) -> None:
        self.locked.append(client_id)

    def add(self, advance) -> None:
        self.added.append(advance)


class _Db:
    async def flush(self) -> None:
        pass


def _service(repo: _Repo, balance: int = 0) -> AdminClientService:
    service = AdminClientService.__new__(AdminClientService)
    service.db, service.repo, service.advances, service.admin_id = _Db(), repo, _Advances(balance), 999
    return service


def test_search_limits_results_and_skips_short_queries() -> None:
    repo = _Repo(found=[SimpleNamespace(id=i) for i in range(40)])
    result = asyncio.run(_service(repo).search("петр"))
    assert len(result.clients) == SEARCH_LIMIT and result.more  # больше 30 - первые 30 и «уточните»
    assert repo.calls == [("name", ["петр"], SEARCH_LIMIT + 1)]
    asyncio.run(_service(repo).search("0010"))
    assert repo.calls[-1] == ("phone", "0010", SEARCH_LIMIT + 1)
    short = _Repo()
    assert asyncio.run(_service(short).search("п")).query.kind is QueryKind.TOO_SHORT and not short.calls


def _enrollment(status: EnrollmentStatus, client=PETROV) -> tuple:
    return SimpleNamespace(status=status), client, None


def test_participants_are_grouped_by_status() -> None:
    rows = [_enrollment(EnrollmentStatus.CONFIRMED), _enrollment(EnrollmentStatus.ATTENDED),
            _enrollment(EnrollmentStatus.APPLICATION), _enrollment(EnrollmentStatus.WAITLIST),
            _enrollment(EnrollmentStatus.CANCELLED_BY_CLIENT), _enrollment(EnrollmentStatus.CANCELLED_BY_ADMIN)]
    view = asyncio.run(_service(_Repo(rows=rows)).participants(5))
    assert (len(view.confirmed), len(view.awaiting), len(view.waitlist), len(view.cancelled)) == (2, 1, 1, 2)
    assert view.seats_left == 7  # 10 мест - 2 закреплено - 1 ждёт оплату; лист ожидания места не занимает
    assert asyncio.run(_service(_Repo(rows=rows)).participants_count(5)) == 4  # без отменивших


def test_advance_correction() -> None:
    service = _service(_Repo(client=PETROV), balance=300_000)
    result = asyncio.run(service.adjust_advance(10, -500_000, "ошибка"))
    assert result.outcome is AdvanceOutcome.NEGATIVE and result.balance == 300_000 and not service.advances.added
    result = asyncio.run(service.adjust_advance(10, 500_000, "подарок за отзыв"))
    assert result.outcome is AdvanceOutcome.DONE and result.balance == 800_000
    [advance] = service.advances.added
    assert (advance.amount, advance.reason, advance.comment, advance.order_id, advance.created_by_telegram_id) == (
        500_000, AdvanceReason.CORRECTION, "подарок за отзыв", None, 999)
    assert service.advances.locked == [10, 10]  # правки одного клиента - по очереди
    assert asyncio.run(service.adjust_advance(99, 100, "x")).outcome is AdvanceOutcome.NOT_FOUND


# --- Экраны --------------------------------------------------------------------------------------------------------

def test_menu_and_search_results() -> None:
    text, markup = views.clients_menu(152)
    assert "Всего в базе: <b>152</b>" in text
    assert _rows(markup) == [[texts.BTN_ADMIN_CLIENT_FIND], [texts.BTN_ADMIN_WAITLISTS], [texts.BTN_ADMIN_BACK]]
    result = SearchResult(SearchQuery(QueryKind.NAME, ("петр",)), [PETROV, COLLEAGUE], more=True)
    text, markup = views.search_results(result, "петр <b>")
    assert "Найдено: 2</b> по «петр &lt;b&gt;»" in text and "первые 30" in text
    assert _rows(markup)[:2] == [["Петров Пётр Петрович · …0010"], ["Петрова Анна Сергеевна · …4567 (не в боте)"]]
    first = _unpack(markup.inline_keyboard[0][0])
    assert (first.action, first.item_id, first.value) == (ClientAdminAction.VIEW, 10, views.BACK_FIND)
    empty, _ = views.search_results(SearchResult(SearchQuery(QueryKind.PHONE, digits="9999"), []), "9999")
    assert "Никого не нашли по «9999»" in empty


def _session(**kw) -> SimpleNamespace:
    fields = dict(id=5, title_override=None, program=SimpleNamespace(title="Имплантация"), capacity=10,
                  start_date=date(2026, 11, 13), end_date=date(2026, 11, 14), days=[])
    return SimpleNamespace(**{**fields, **kw})


def _card(client=PETROV, **kw) -> AdminClientCard:
    order = SimpleNamespace(id=77, number=100001, status=OrderStatus.PARTIALLY_PAID)
    fields = dict(marketing=True, advance=2_000_000,
                  enrollments=[(SimpleNamespace(status=EnrollmentStatus.CONFIRMED), _session(), _session().program,
                                None, order)],
                  orders=[order], waiting=[(SimpleNamespace(title="Эндодонтия"), datetime(2026, 10, 20, tzinfo=UTC))])
    return AdminClientCard(client, **{**fields, **kw})


def test_client_card() -> None:
    text, markup = views.client_card(_card(), TZ, f"{views.BACK_PARTICIPANTS}5")
    for part in ("👤 <b>Петров Пётр Петрович</b>", "📞 +79990000010", "Telegram: @petrov", "petrov@mail.ru",
                 "Санкт-Петербург · Клиника «Улыбка»", "Хирургия", "Рассылки: да", format_rub(2_000_000),
                 "В базе с 12 октября 2026", "Имплантация · 13–14 ноября 2026 · ✅ Место закреплено · №100001",
                 "Ждёт курс:</b> Эндодонтия (с 20 октября)"):
        assert part in text, part
    assert _rows(markup) == [["🧾 №100001"], [texts.BTN_ADMIN_CLIENT_WRITE, texts.BTN_ADMIN_CLIENT_ADVANCE],
                             [texts.BTN_ADMIN_CLIENT_BACK]]
    order = OrderAdminCb.unpack(markup.inline_keyboard[0][0].callback_data)
    assert (order.action, order.order_id) == (OrderAdminAction.VIEW, 77)
    write = _unpack(markup.inline_keyboard[1][0])
    assert (write.action, write.item_id, write.value) == (ClientAdminAction.WRITE, 10, "p5")  # «Назад» помнит путь
    back = _unpack(markup.inline_keyboard[2][0])
    assert (back.action, back.item_id) == (ClientAdminAction.PARTICIPANTS, 5)


def test_client_card_telegram_states_and_empty_card() -> None:
    blocked = SimpleNamespace(**{**vars(PETROV), "is_bot_blocked": True})
    assert texts.ADMIN_CLIENT_TG_BLOCKED in views.client_card(_card(blocked), TZ, "")[0]
    no_username = SimpleNamespace(**{**vars(PETROV), "tg_username": None})
    assert texts.ADMIN_CLIENT_TG_NO_USERNAME in views.client_card(_card(no_username), TZ, "")[0]
    text, markup = views.client_card(_card(COLLEAGUE, marketing=False, advance=0, enrollments=[], orders=[],
                                           waiting=[]), TZ, "")
    assert texts.ADMIN_CLIENT_TG_NONE in text and texts.ADMIN_CLIENT_NO_ENROLLMENTS in text
    assert "Аванс" not in text and "Ждёт курс" not in text and "Рассылки: нет" in text
    assert _rows(markup)[0] == [texts.BTN_ADMIN_CLIENT_WRITE, texts.BTN_ADMIN_CLIENT_ADVANCE]


def test_back_button_follows_the_path() -> None:
    def target(back: str):
        button = views.back_button(back)[0]
        if button.callback_data.startswith("aord:"):  # кнопка раздела «Заказы»
            data = OrderAdminCb.unpack(button.callback_data)
            return data.action, data.order_id
        data = ClientAdminCb.unpack(button.callback_data)
        return data.action, data.item_id

    assert target("f") == (ClientAdminAction.FIND, 0)
    assert target("p5") == (ClientAdminAction.PARTICIPANTS, 5)
    assert target("x5") == (ClientAdminAction.CANCELLED, 5)
    assert target("w3") == (ClientAdminAction.WAITLIST, 3)
    assert target("o77") == (OrderAdminAction.VIEW, 77)
    assert target("") == target("p") == target("zzz") == (ClientAdminAction.MENU, 0)  # непонятное - в меню


def test_participants_screens() -> None:
    order = SimpleNamespace(number=100001)
    view = SessionParticipants(
        _session(), confirmed=[(SimpleNamespace(status=EnrollmentStatus.CONFIRMED), PETROV, order)],
        awaiting=[(SimpleNamespace(status=EnrollmentStatus.APPLICATION), COLLEAGUE, order)], waitlist=[],
        cancelled=[(SimpleNamespace(status=EnrollmentStatus.CANCELLED_BY_CLIENT), COLLEAGUE, None)],
    )
    text, markup = views.participants(view)
    assert "Место закреплено: <b>1</b>" in text and "Ждём оплату: <b>1</b>" in text
    assert "Свободно мест: <b>8</b> из 10" in text
    assert _rows(markup) == [["✅ Петров П. П. · №100001"], ["⏳ Петрова А. С. · №100001"], ["✖️ Отменили · 1"],
                             [texts.BTN_ADMIN_TO_SESSION]]
    assert _unpack(markup.inline_keyboard[0][0]).value == "p5"
    back = DictCb.unpack(markup.inline_keyboard[-1][0].callback_data)
    assert (back.kind, back.action, back.item_id) == (DictKind.SESSIONS, DictAction.VIEW, 5)
    text, markup = views.cancelled_participants(view)
    assert "Всего: <b>1</b>" in text and _rows(markup) == [["✖️ Петрова А. С."], [texts.BTN_ADMIN_TO_PARTICIPANTS]]
    assert _unpack(markup.inline_keyboard[0][0]).value == "x5"
    empty = SessionParticipants(_session(capacity=None), [], [], [], [])
    text, markup = views.participants(empty)
    assert texts.ADMIN_PARTICIPANTS_EMPTY in text and "Свободно" not in text
    assert _rows(markup) == [[texts.BTN_ADMIN_TO_SESSION]]


def test_waitlist_screens() -> None:
    program = SimpleNamespace(id=3, title="Эндодонтия")
    text, markup = views.waitlists([(program, 4)])
    assert _rows(markup) == [["Эндодонтия · 4"], [texts.BTN_ADMIN_TO_CLIENTS]]
    assert texts.ADMIN_WAITLISTS_EMPTY == views.waitlists([])[0]
    rows = [(PETROV, datetime(2026, 10, 20, 9, 0, tzinfo=UTC))]
    text, markup = views.course_waitlist(program, rows, TZ, from_course=True)
    assert "Ждут нового проведения: <b>1</b>" in text
    assert _rows(markup) == [["Петров П. П. · с 20 октября"], [texts.BTN_ADMIN_INVITED], [texts.BTN_ADMIN_TO_COURSE]]
    assert _unpack(markup.inline_keyboard[0][0]).value == "w3"
    back = DictCb.unpack(markup.inline_keyboard[-1][0].callback_data)
    assert (back.kind, back.item_id) == (DictKind.COURSES, 3)
    _, markup = views.course_waitlist(program, rows, TZ, from_course=False)
    assert _rows(markup)[-1] == [texts.BTN_ADMIN_TO_WAITLISTS]
    text, _ = views.course_waitlist(program, [], TZ, from_course=False)
    assert "никто не ждёт" in text
    text, markup = views.invited_confirm(program, 4, from_course=True)
    assert "всех (4)" in text and _unpack(markup.inline_keyboard[0][0]).action is ClientAdminAction.INVITED_OK


def test_advance_and_message_screens_carry_the_token() -> None:
    text, markup = views.advance_confirm(PETROV, AdvanceInput(-500_000, "ошибка <x>"), 2_000_000, "tok", "f")
    assert f"Списать {format_rub(500_000)} - Петров П. П.?" in text and "ошибка &lt;x&gt;" in text
    assert f"Аванс станет: <b>{format_rub(1_500_000)}</b>" in text
    yes, cancel = markup.inline_keyboard[0]
    assert (_unpack(yes).action, _unpack(yes).value) == (ClientAdminAction.ADVANCE_OK, "tok")
    assert (_unpack(cancel).action, _unpack(cancel).value) == (ClientAdminAction.CANCEL, "f")
    assert "Начислить" in views.advance_confirm(PETROV, AdvanceInput(100, "x"), 0, "t", "")[0]
    send = _unpack(views.preview_buttons(10, "abc", "p5").inline_keyboard[0][0])
    assert (send.action, send.value) == (ClientAdminAction.SEND, "abc")


def test_buttons_in_session_and_course_cards() -> None:
    from stubbot.services.admin_registry import ADMIN_SPECS

    markup = admin_views.session_card_markup(ADMIN_SPECS[DictKind.SESSIONS], 7, "registration_open", participants=3)
    assert _rows(markup)[1] == ["👥 Участники · 3"]
    assert _unpack(markup.inline_keyboard[1][0]).action is ClientAdminAction.PARTICIPANTS
    assert "Участники" not in str(_rows(admin_views.session_card_markup(ADMIN_SPECS[DictKind.SESSIONS], 7, "full")))
    markup = admin_views.course_card_markup(ADMIN_SPECS[DictKind.COURSES], 3, has_program=False, waiting=4)
    waitlist = next(b for row in markup.inline_keyboard for b in row if b.text == "🕒 Лист ожидания · 4")
    assert (_unpack(waitlist).item_id, _unpack(waitlist).value) == (3, admin_views.FROM_COURSE)
    no_one = admin_views.course_card_markup(ADMIN_SPECS[DictKind.COURSES], 3, has_program=False)
    assert "Лист ожидания" not in str(_rows(no_one))


# --- Доставка сообщения клиенту ------------------------------------------------------------------------------------

class _Bot:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.sent = error, []

    async def send_message(self, chat_id, text, reply_markup=None):
        if self.error:
            raise self.error
        self.sent.append((chat_id, text))


def test_delivery_to_client() -> None:
    message = Outgoing(OutgoingKind.TEXT, "Здравствуйте!")
    bot = _Bot()
    assert asyncio.run(deliver_to_client(bot, None, PETROV, message)) is Delivery.DELIVERED
    assert bot.sent == [(111, "Здравствуйте!")]
    assert asyncio.run(deliver_to_client(_Bot(), None, COLLEAGUE, message)) is Delivery.NO_TELEGRAM
    failed = _Bot(TelegramBadRequest(method=None, message="Bad Request: can't parse entities"))
    assert asyncio.run(deliver_to_client(failed, None, PETROV, message)) is Delivery.FAILED
    report = admin_orders_views.delivery_report(Delivery.NO_TELEGRAM, COLLEAGUE)
    assert "нет в боте" in report and "+79991234567" in report


def test_popups_fit_telegram_limit() -> None:
    assert len(texts.ADMIN_CLIENT_UNAVAILABLE) <= 200 and "<" not in texts.ADMIN_CLIENT_UNAVAILABLE
    negative = texts.ADMIN_CLIENT_ADVANCE_NEGATIVE.format(balance=format_rub(99_999_900))
    assert len(negative) <= 200 and "<" not in negative  # и всплывающее окно, и ответ на ввод
