"""Заказы в админке и уведомления админам (полная версия, этап 1, шаг 4.1). Без БД и Telegram."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from stubbot.config import Settings
from stubbot.db.enums import (
    AdvanceReason,
    CancelChoice,
    EnrollmentStatus,
    OrderStatus,
    PayerType,
    PaymentMethod,
    PaymentStatus,
    PriceUnit,
)
from stubbot.services.admin_orders import AdminOrderCard, AdminOrderService, OrderCategory, Outcome
from stubbot.services.enrollments import CancelOutcome, CancelResult, MyApplication
from stubbot.services.schedule import SessionCard
from stubbot.tg import admin_orders_views as views
from stubbot.tg import admin_views, render, texts
from stubbot.tg.callbacks import ClientAdminAction, ClientAdminCb, OrderAdminAction, OrderAdminCb
from stubbot.tg.outgoing import CAPTION_LIMIT, Outgoing, OutgoingKind, SendOnce, from_message
from stubbot.utils.money import format_rub

TZ = "Europe/Moscow"
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 21)
PAYER = SimpleNamespace(id=10, full_name="Петров Пётр Петрович", phone="+79990000010", telegram_id=111)
COLLEAGUE = SimpleNamespace(id=50, full_name="Петрова Анна Сергеевна", phone="+79991234567", telegram_id=None)


def _order(**kw) -> SimpleNamespace:
    fields = dict(id=77, number=100001, payer_client_id=PAYER.id, session_id=5, price_option_id=None,
                  status=OrderStatus.AWAITING_PAYMENT, payer_type=None, total_amount=8_000_000, currency="RUB",
                  due_at=NOW + timedelta(hours=72), requisites_file_id=None, invoice_sent_at=None, paid_at=None,
                  created_at=NOW, cancelled_at=None, cancel_reason=None, cancel_choice=None)
    return SimpleNamespace(**{**fields, **kw})


def _row(client, status=EnrollmentStatus.APPLICATION, enrolled_by=None) -> SimpleNamespace:
    return SimpleNamespace(id=client.id, client_id=client.id, status=status, enrolled_by_client_id=enrolled_by,
                           confirmed_at=None, cancelled_at=None, cancel_reason=None, payment_status=PaymentStatus.UNPAID)


# --- Сервис --------------------------------------------------------------------------------------------------------

class _Orders:
    def __init__(self, order, paid: int = 0, participants=(), settled: bool = False) -> None:
        self.order, self.paid, self.settled = order, paid, settled
        self._participants = list(participants)
        self.payments: list = []

    async def lock(self, order_id: int):
        return self.order if self.order.id == order_id else None

    async def get(self, order_id: int):
        return await self.lock(order_id)

    async def paid_amounts(self, order_ids):
        return {order_id: self.paid for order_id in order_ids} if self.paid else {}

    def add_payment(self, payment) -> None:
        self.payments.append(payment)

    async def participants(self, order_id: int, lock: bool = False):
        return self._participants

    async def money_settled(self, order_id: int) -> bool:
        return self.settled


class _Advances:
    def __init__(self, balance: int = 0) -> None:
        self.start = balance
        self.added: list = []
        self.locked: list[int] = []

    async def balance(self, client_id: int) -> int:
        return self.start + sum(a.amount for a in self.added)

    async def lock_client(self, client_id: int) -> None:
        self.locked.append(client_id)

    def add(self, advance) -> None:
        self.added.append(advance)


class _Enrollments:
    def __init__(self) -> None:
        self.course_waitlist: list[tuple[int, int]] = []

    async def get_session(self, session_id: int):
        return SimpleNamespace(id=session_id, program_id=3)

    async def put_on_course_waitlist(self, client_id: int, program_id: int) -> None:
        self.course_waitlist.append((client_id, program_id))


class _Db:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def flush(self) -> None:
        if self.fail:
            raise IntegrityError("INSERT", {}, Exception("uq_payments_idempotency_key"))


def _service(order, paid: int = 0, participants=(), settled: bool = False, balance: int = 0,
             fail: bool = False) -> AdminOrderService:
    service = AdminOrderService.__new__(AdminOrderService)
    service.db = _Db(fail)
    service.orders = _Orders(order, paid, participants, settled)
    service.advances = _Advances(balance)
    service.enrollments = _Enrollments()
    service.admin_id = 999
    return service


def _run(coro):
    return asyncio.run(coro)


def test_partial_payment_secures_the_seats() -> None:
    order = _order()
    rows = [(_row(PAYER), PAYER), (_row(COLLEAGUE, enrolled_by=PAYER.id), COLLEAGUE)]
    service = _service(order, participants=rows)
    result = _run(service.mark_payment(77, 2_000_000, PaymentMethod.CASH, "tok", NOW))
    assert result.outcome is Outcome.DONE and (result.paid, result.full) == (2_000_000, False)
    assert order.status is OrderStatus.PARTIALLY_PAID and order.paid_at is None
    [payment] = service.orders.payments
    assert (payment.amount, payment.method, payment.idempotency_key, payment.created_by_telegram_id) == (
        2_000_000, PaymentMethod.CASH, "admin:tok", 999)
    # Любая оплата закрепляет место - у обоих участников пары.
    assert [row.status for row, _ in rows] == [EnrollmentStatus.CONFIRMED] * 2
    assert [row.payment_status for row, _ in rows] == [PaymentStatus.PARTIAL] * 2


def test_full_payment_and_limits() -> None:
    order = _order(status=OrderStatus.PARTIALLY_PAID)
    result = _run(_service(order, paid=2_000_000).mark_payment(77, 6_000_000, PaymentMethod.INVOICE, "t", NOW))
    assert result.full and order.status is OrderStatus.PAID and order.paid_at == NOW
    # Больше остатка - нельзя (переплата - через аванс); отменённый заказ - нельзя; повтор той же отметки - дубль.
    too_much = _service(_order(), paid=0)
    assert _run(too_much.mark_payment(77, 8_000_001, PaymentMethod.CASH, "t", NOW)).outcome is Outcome.TOO_MUCH
    assert not too_much.orders.payments
    cancelled = _order(status=OrderStatus.CANCELLED)
    assert _run(_service(cancelled).mark_payment(77, 100, PaymentMethod.CASH, "t", NOW)).outcome is Outcome.NOT_ALLOWED
    duplicate = _run(_service(_order(), fail=True).mark_payment(77, 100, PaymentMethod.CASH, "t", NOW))
    assert duplicate.outcome is Outcome.DUPLICATE


def test_order_without_amount_takes_any_payment_and_total_later() -> None:
    order = _order(total_amount=None)
    result = _run(_service(order).mark_payment(77, 3_000_000, PaymentMethod.CARD, "t", NOW))
    assert result.outcome is Outcome.DONE and not result.full and order.status is OrderStatus.PARTIALLY_PAID
    open_order = _order(total_amount=None)
    assert _run(_service(open_order, paid=3_000_000).set_total(77, 2_000_000)) is None  # меньше оплаченного
    assert _run(_service(open_order, paid=3_000_000).set_total(77, 4_500_000)) is open_order
    assert open_order.total_amount == 4_500_000
    assert _run(_service(_order()).set_total(77, 1)) is None  # указанную сумму не меняем


def test_remove_booking_without_payment_puts_both_on_course_waitlist() -> None:
    order = _order()
    rows = [(_row(PAYER), PAYER), (_row(COLLEAGUE, enrolled_by=PAYER.id), COLLEAGUE)]
    service = _service(order, participants=rows)
    result = _run(service.remove_booking(77, NOW))
    assert result.outcome is Outcome.DONE and result.course_waitlist and result.paid == 0
    assert order.status is OrderStatus.CANCELLED and order.cancelled_at == NOW
    assert [row.status for row, _ in rows] == [EnrollmentStatus.CANCELLED_BY_ADMIN] * 2
    assert service.enrollments.course_waitlist == [(PAYER.id, 3), (COLLEAGUE.id, 3)]
    assert _run(service.remove_booking(77, NOW)).outcome is Outcome.NOT_ALLOWED  # уже снята


def test_remove_paid_booking_leaves_money_to_settle() -> None:
    order = _order(status=OrderStatus.PARTIALLY_PAID)
    service = _service(order, paid=2_000_000, participants=[(_row(PAYER, EnrollmentStatus.CONFIRMED), PAYER)])
    result = _run(service.remove_booking(77, NOW))
    assert result.outcome is Outcome.DONE and not result.course_waitlist and result.paid == 2_000_000
    assert not service.enrollments.course_waitlist


def test_credit_and_refund_of_cancelled_order() -> None:
    cancelled = _order(status=OrderStatus.CANCELLED)
    service = _service(cancelled, paid=2_000_000)
    result = _run(service.credit_advance(77))
    assert result.outcome is Outcome.DONE and (result.amount, result.balance) == (2_000_000, 2_000_000)
    [advance] = service.advances.added
    assert (advance.amount, advance.reason, advance.order_id, advance.client_id) == (
        2_000_000, AdvanceReason.CANCELLED_ORDER, 77, PAYER.id)
    assert service.advances.locked == [PAYER.id]  # аванс одного клиента меняется по очереди
    refund = _service(_order(status=OrderStatus.CANCELLED), paid=2_000_000)
    assert _run(refund.mark_refunded(77)).balance == 0
    assert [(a.amount, a.reason) for a in refund.advances.added] == [
        (2_000_000, AdvanceReason.CANCELLED_ORDER), (-2_000_000, AdvanceReason.REFUND)]
    # Уже распределены, оплаты не было, заказ не отменён - нельзя.
    for service in (_service(_order(status=OrderStatus.CANCELLED), paid=100, settled=True),
                    _service(_order(status=OrderStatus.CANCELLED)), _service(_order(), paid=100)):
        assert _run(service.credit_advance(77)).outcome is Outcome.NOT_ALLOWED


def test_apply_advance_up_to_balance_and_left() -> None:
    order = _order(status=OrderStatus.PARTIALLY_PAID)
    service = _service(order, paid=2_000_000, balance=500_000, participants=[(_row(PAYER), PAYER)])
    result = _run(service.apply_advance(77, NOW))
    assert result.outcome is Outcome.DONE and result.amount == 500_000 and result.balance == 0
    [payment] = service.orders.payments
    assert (payment.method, payment.amount) == (PaymentMethod.ADVANCE, 500_000)
    assert [(a.amount, a.reason) for a in service.advances.added] == [(-500_000, AdvanceReason.APPLIED_TO_ORDER)]
    big = _service(_order(), paid=7_000_000, balance=5_000_000)
    assert _run(big.apply_advance(77, NOW)).amount == 1_000_000  # не больше остатка
    assert _run(_service(_order(), balance=0).apply_advance(77, NOW)).outcome is Outcome.NO_ADVANCE
    assert _run(_service(_order(total_amount=None), balance=100).apply_advance(77, NOW)).outcome is Outcome.NOT_ALLOWED


# --- Карточка и разделы --------------------------------------------------------------------------------------------

def _session() -> SimpleNamespace:
    return SimpleNamespace(id=5, title_override=None, program=SimpleNamespace(title="Имплантация <базовый>"),
                           start_date=date(2026, 11, 13), end_date=date(2026, 11, 14), days=[], venue=None)


def _card(order=None, paid: int = 0, advance: int = 0, settled: bool = False, participants=None,
          price=None) -> AdminOrderCard:
    order = order or _order()
    participants = participants if participants is not None else [(_row(PAYER), PAYER)]
    return AdminOrderCard(order, _session(), price, PAYER, participants, paid, advance, settled)


def _rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def test_card_category_and_amounts() -> None:
    assert _card().category(NOW) is OrderCategory.AWAITING
    assert _card(_order(due_at=NOW - timedelta(minutes=1))).category(NOW) is OrderCategory.OVERDUE
    assert _card(_order(status=OrderStatus.PARTIALLY_PAID), paid=1).category(NOW) is OrderCategory.PARTIAL
    assert _card(_order(status=OrderStatus.PAID), paid=8_000_000).category(NOW) is OrderCategory.PAID
    cancelled = _order(status=OrderStatus.CANCELLED)
    assert _card(cancelled, paid=100).category(NOW) is OrderCategory.MONEY
    assert _card(cancelled, paid=100, settled=True).category(NOW) is None
    assert _card(paid=2_000_000).left == 6_000_000 and _card(_order(total_amount=None)).left is None
    assert _card(paid=7_000_000, advance=5_000_000).advance_to_apply == 1_000_000


def test_orders_menu_and_list() -> None:
    counts = {OrderCategory.AWAITING: 5, OrderCategory.OVERDUE: 2, OrderCategory.PARTIAL: 1, OrderCategory.PAID: 12,
              OrderCategory.MONEY: 1}
    text, markup = views.orders_menu(counts)
    assert "Ждут оплаты: <b>5</b> (просрочено: 2)" in text and "не распределены: <b>1</b>" in text
    assert _rows(markup)[0] == ["⏳ Ждут оплаты · 5"] and _rows(markup)[-2] == [texts.BTN_ADMIN_ORDER_FIND]
    assert "просрочено" not in views.orders_menu({**counts, OrderCategory.OVERDUE: 0})[0]
    orders = [(_order(id=i, number=100000 + i), PAYER) for i in range(1, 11)]
    text, markup = views.order_list(OrderCategory.PAID, orders, page=1)
    assert "Заказов: <b>10</b>" in text and "ещё не прошли" in text
    rows = _rows(markup)
    assert rows[0] == [f"№100009 · Петров П. П. · {format_rub(8_000_000)}"] and rows[2] == ["◀️", "2/2"]
    assert rows[-1] == [texts.BTN_ADMIN_TO_ORDERS]


def test_card_text_and_buttons_waiting_for_payment() -> None:
    participants = [(_row(PAYER), PAYER), (_row(COLLEAGUE, enrolled_by=PAYER.id), COLLEAGUE)]
    order = _order(payer_type=PayerType.ORGANIZATION, requisites_file_id="FILE", due_at=NOW - timedelta(hours=1))
    text, markup = views.order_card(_card(order, advance=500_000, participants=participants), TZ, NOW)
    assert "Заказ №100001</b> · ⏳ ждёт оплаты" in text and "Имплантация &lt;базовый&gt;" in text
    assert "Петров Пётр Петрович · +79990000010" in text and "Петрова А. С. (не в боте)" in text
    assert texts.ADMIN_ORDER_DUE_LATE in text and texts.ADMIN_ORDER_ORGANIZATION in text
    assert texts.ADMIN_ORDER_INVOICE_NONE in text and format_rub(500_000) in text
    assert _rows(markup) == [[texts.BTN_ADMIN_ORDER_INVOICE], [texts.BTN_ADMIN_ORDER_PAY],
                             [texts.BTN_ADMIN_ORDER_APPLY.format(amount=format_rub(500_000))],
                             [texts.BTN_ADMIN_ORDER_REQUISITES],
                             [texts.BTN_ADMIN_ORDER_CLIENT, texts.BTN_ADMIN_ORDER_WRITE],
                             [texts.BTN_ADMIN_ORDER_REMOVE], [texts.BTN_ADMIN_TO_ORDERS]]
    to_client = ClientAdminCb.unpack(markup.inline_keyboard[4][0].callback_data)
    assert (to_client.action, to_client.item_id, to_client.value) == (ClientAdminAction.VIEW, PAYER.id, "o77")
    back = OrderAdminCb.unpack(markup.inline_keyboard[-1][0].callback_data)
    assert (back.action, back.value) == (OrderAdminAction.LIST, OrderCategory.OVERDUE.value)


def test_card_of_order_without_amount_and_of_cancelled_paid_order() -> None:
    _, markup = views.order_card(_card(_order(total_amount=None)), TZ, NOW)
    assert [texts.BTN_ADMIN_ORDER_TOTAL] in _rows(markup)
    cancelled = _order(status=OrderStatus.CANCELLED, cancelled_at=NOW, cancel_reason="Отменена клиентом в боте",
                       cancel_choice=CancelChoice.ADVANCE)
    text, markup = views.order_card(_card(cancelled, paid=2_000_000), TZ, NOW)
    assert "Клиент выбрал: <b>оставить на аванс</b>" in text and texts.ADMIN_ORDER_MONEY_PENDING in text
    assert _rows(markup) == [[texts.BTN_ADMIN_ORDER_CREDIT.format(amount=format_rub(2_000_000))],
                             [texts.BTN_ADMIN_ORDER_REFUND], [texts.BTN_ADMIN_ORDER_CLIENT, texts.BTN_ADMIN_ORDER_WRITE],
                             [texts.BTN_ADMIN_TO_ORDERS]]  # бронь уже снята - снимать нечего
    text, markup = views.order_card(_card(cancelled, paid=2_000_000, settled=True), TZ, NOW)
    assert texts.ADMIN_ORDER_MONEY_SETTLED in text and texts.BTN_ADMIN_ORDER_REFUND not in str(_rows(markup))


def test_amount_and_method_screens_carry_the_token() -> None:
    text, markup = views.ask_amount(_card(paid=2_000_000))
    assert "осталось " + format_rub(6_000_000) in text and _rows(markup)[0] == [
        texts.BTN_ADMIN_ORDER_PAY_FULL.format(amount=format_rub(6_000_000))]
    text, markup = views.ask_amount(_card(_order(total_amount=None)))
    assert texts.ADMIN_ORDER_AMOUNT_HINT in text and _rows(markup) == [[texts.BTN_ADMIN_CANCEL]]
    text, markup = views.ask_method(_order(), 2_000_000, "abc")
    assert _rows(markup)[:3] == [["🧾 По счёту"], ["💵 Наличные"], ["💳 Карта, СБП"]]
    values = [OrderAdminCb.unpack(row[0].callback_data).value for row in markup.inline_keyboard[:3]]
    assert values == ["invoice.abc", "cash.abc", "card.abc"]
    send = OrderAdminCb.unpack(views.preview_buttons(77, "xyz").inline_keyboard[0][0].callback_data)
    assert (send.action, send.value) == (OrderAdminAction.SEND, "xyz")
    opened = OrderAdminCb.unpack(views.open_order(77).inline_keyboard[0][0].callback_data)
    assert (opened.action, opened.order_id, opened.value) == (OrderAdminAction.VIEW, 77, views.NEW_SCREEN)


def test_confirm_screens() -> None:
    participants = [(_row(PAYER), PAYER), (_row(COLLEAGUE, enrolled_by=PAYER.id), COLLEAGUE)]
    text, _ = views.remove_confirm(_card(participants=participants))
    assert "Петров П. П., Петрова А. С." in text and texts.ADMIN_ORDER_REMOVE_UNPAID in text
    assert "оплачено " + format_rub(2_000_000) in views.remove_confirm(_card(paid=2_000_000))[0]
    assert format_rub(2_000_000) in views.credit_confirm(_card(_order(status=OrderStatus.CANCELLED), paid=2_000_000))[0]
    text, _ = views.apply_confirm(_card(paid=7_000_000, advance=5_000_000))
    assert f"{format_rub(1_000_000)} в заказ №100001" in text and "останется " + format_rub(4_000_000) in text


# --- Уведомления и сообщения клиенту -------------------------------------------------------------------------------

def test_admin_notifications() -> None:
    card = SessionCard(session=_session(), seats_left=None)
    price = SimpleNamespace(id=2, label="Для двух коллег", amount=8_000_000, unit=PriceUnit.PER_GROUP)
    text, markup = views.notify_enrolled(card, _order(), PAYER, COLLEAGUE, price, TZ)
    assert "Новая запись</b> · заказ №100001" in text and "С коллегой: Петрова Анна Сергеевна" in text
    assert "(ещё не в боте)" in text and "за двоих" in text and "24 октября, 12:00" in text
    assert markup.inline_keyboard[0][0].text == texts.BTN_ADMIN_OPEN_ORDER
    text, markup = views.notify_enrolled(card, None, PAYER, None, None, TZ)
    assert "Лист ожидания" in text and markup is None and "коллегой" not in text
    text, _ = views.notify_payer_type(_order(payer_type=PayerType.INDIVIDUAL), PAYER)
    assert "физлицо" in text and "Отправить счёт" in text
    assert "юрлицо" in views.notify_payer_type(_order(payer_type=PayerType.ORGANIZATION), PAYER)[0]
    caption, _ = views.notify_requisites(_order(), PAYER)
    assert "Реквизиты</b> · заказ №100001" in caption and render.visible_length(caption) <= CAPTION_LIMIT


def test_cancel_notifications() -> None:
    app = MyApplication(_row(PAYER), _session(), _session().program, None, _order())
    paid = CancelResult(CancelOutcome.CANCELLED, order=_order(), paid=2_000_000, choice=CancelChoice.REFUND)
    text, markup = views.notify_cancelled(app, paid, PAYER)
    assert "заказ №100001" in text and "клиент выбрал: <b>вернуть деньги</b>" in text and markup is not None
    pair = CancelResult(CancelOutcome.CANCELLED, order=_order(), colleagues=(COLLEAGUE,), course_waitlist=True)
    text, _ = views.notify_cancelled(app, pair, PAYER)
    assert "(и коллега)" in text and "оба в листе ожидания курса" in text
    left, markup = views.notify_cancelled(app, CancelResult(CancelOutcome.CANCELLED, waitlist=True), PAYER)
    assert "Выход из листа ожидания" in left and markup is None


def test_client_messages() -> None:
    session = _session()
    partial = render.payment_received(session, _order(), 2_000_000, full=False)
    assert f"Оплачено {format_rub(2_000_000)} из {format_rub(8_000_000)}" in partial and "закреплено" in partial
    full = render.payment_received(session, _order(), 8_000_000, full=True, from_advance=500_000)
    assert texts.PAYMENT_RECEIVED_FULL in full and f"{format_rub(500_000)} зачтено из Вашего аванса" in full
    assert texts.BOOKING_REMOVED_UNPAID in render.booking_removed(session, _order(), 0)
    assert format_rub(2_000_000) in render.booking_removed(session, _order(), 2_000_000)
    assert "Имплантация &lt;базовый&gt;" in render.booking_removed_colleague(session)
    caption = render.invoice_caption(_order(), 6_000_000, TZ)
    assert "Счёт по заказу №100001" in caption and format_rub(6_000_000) in caption and "24 октября" in caption
    assert format_rub(2_000_000) in render.advance_credited(2_000_000)


# --- Отправка сообщений, уведомления, меню ---------------------------------------------------------------------

def _message(**kw) -> SimpleNamespace:
    fields = dict(text=None, caption=None, document=None, photo=None, html_text="")
    return SimpleNamespace(**{**fields, **kw})


def test_outgoing_from_admin_message() -> None:
    text = from_message(_message(text="Привет", html_text="<b>Привет</b>"))
    assert (text.kind, text.text, text.limit) == (OutgoingKind.TEXT, "<b>Привет</b>", 4096)
    doc = from_message(_message(document=SimpleNamespace(file_id="DOC"), caption="Счёт", html_text="Счёт"))
    assert (doc.kind, doc.file_id, doc.text, doc.limit) == (OutgoingKind.DOCUMENT, "DOC", "Счёт", CAPTION_LIMIT)
    photo = from_message(_message(photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="big")]))
    assert (photo.kind, photo.file_id, photo.text) == (OutgoingKind.PHOTO, "big", "")
    assert from_message(_message()) is None  # стикер, голосовое - не отправляем
    assert Outgoing.from_data(doc.to_data()) == doc


def test_send_once_lets_only_the_first_tap_through() -> None:
    guard = SendOnce(size=2)
    assert guard.claim("a") and not guard.claim("a")
    assert guard.claim("b") and guard.claim("c") and guard.claim("a")  # старые ключи вытесняются


def test_notifications_go_to_owner_and_admins_once() -> None:
    settings = Settings(db_user="u", db_password="p", db_name="d", owner_telegram_id=1, admin_ids=[2, 1, 3])
    assert settings.admin_chat_ids == [1, 2, 3]


def test_admin_menu_shows_orders_only_with_enrollment(monkeypatch) -> None:
    monkeypatch.setattr(admin_views, "get_settings", lambda: SimpleNamespace(applications_enabled=True))
    text, markup = admin_views.menu()
    assert _rows(markup)[0] == [texts.BTN_ADMIN_ORDERS, texts.BTN_ADMIN_CLIENTS] and "Заказы" in text
    assert "Клиенты" in text
    monkeypatch.setattr(admin_views, "get_settings", lambda: SimpleNamespace(applications_enabled=False))
    text, markup = admin_views.menu()
    assert texts.BTN_ADMIN_ORDERS not in str(_rows(markup)) and "Заказы" not in text
    assert texts.BTN_ADMIN_CLIENTS not in str(_rows(markup))


def test_popups_fit_telegram_limit() -> None:
    for popup in (texts.ADMIN_ORDER_UNAVAILABLE, texts.ADMIN_ORDER_DUPLICATE, texts.ADMIN_ORDER_NO_ADVANCE,
                  texts.ADMIN_ORDER_NO_REQUISITES, texts.ADMIN_ORDER_EXPIRED,
                  texts.ADMIN_ORDER_AMOUNT_TOO_MUCH.format(left=format_rub(99_999_900))):
        assert len(popup) <= 200 and "<" not in popup
