"""Онлайн-оплата (полная версия, этап 1, шаг 7: каркас под ЮKassa). Без БД, Telegram и сети."""

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from stubbot.config import OnlinePayments as Mode
from stubbot.config import Settings
from stubbot.db.enums import OrderStatus, PayerType, PaymentMethod, PaymentProvider, PaymentState
from stubbot.payments import GatewayConfigError, build_receipt, create_gateway, receipts_enabled
from stubbot.payments.fake import RESTARTED, FakeGateway
from stubbot.payments.gateway import GatewayError, GatewayPayment, GatewayStatus, PaymentRequest, Receipt
from stubbot.payments.yookassa import YooKassaGateway, kopecks, money, parse_payment, request_body
from stubbot.services import online_payments as service_module
from stubbot.services.admin_orders import AdminOrderCard
from stubbot.services.online_payments import (
    CREATE_GRACE,
    KEY_PREFIX,
    LINK_TTL,
    NOT_CREATED,
    Applied,
    OnlinePaymentService,
    Prepared,
    Problem,
    StartOutcome,
    check_interval,
)
from stubbot.tg import admin_orders_views, keyboards, render, texts
from stubbot.tg import online_payments as coordinator_module
from stubbot.tg.callbacks import PayAction, PayCb
from stubbot.tg.online_payments import OnlinePayments
from stubbot.utils.money import format_rub

TZ = "Europe/Moscow"
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
PAYER = SimpleNamespace(id=10, full_name="Петров Пётр Петрович", phone="+79990000010", telegram_id=111,
                        email="petrov@mail.ru")
SESSION = SimpleNamespace(id=5, title_override=None, program=SimpleNamespace(title="Имплантация <базовый>"),
                          start_date=date(2026, 11, 13), end_date=date(2026, 11, 14), days=[], venue=None)


def _order(**kw) -> SimpleNamespace:
    fields = dict(id=77, number=100001, payer_client_id=PAYER.id, session_id=5, price_option_id=None,
                  status=OrderStatus.AWAITING_PAYMENT, payer_type=PayerType.INDIVIDUAL, total_amount=8_000_000,
                  currency="RUB", due_at=NOW + timedelta(hours=72), requisites_file_id=None, invoice_sent_at=None,
                  paid_at=None, created_at=NOW, cancelled_at=None, cancel_reason=None, cancel_choice=None)
    return SimpleNamespace(**{**fields, **kw})


def _payment(**kw) -> SimpleNamespace:
    fields = dict(id=5, order_id=77, amount=8_000_000, currency="RUB", method=PaymentMethod.CARD_ONLINE,
                  provider=PaymentProvider.YOOKASSA, state=PaymentState.PENDING, provider_payment_id="yk-1",
                  idempotency_key="online:abc", confirmation_url="https://pay.example/yk-1", checked_at=None,
                  cancel_reason=None, paid_at=None, created_at=NOW - timedelta(minutes=5))
    return SimpleNamespace(**{**fields, **kw})


def _settings(**values) -> Settings:
    base = dict(db_user="u", db_password="p", db_name="d", owner_telegram_id=1, applications_enabled=True)
    return Settings(_env_file=None, **{**base, **values})


def _rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def _run(coro):
    return asyncio.run(coro)


# --- ЮKassa: запрос и ответ ----------------------------------------------------------------------------------------

def test_money_in_api_format() -> None:
    assert money(8_000_000) == "80000.00" and money(4_550) == "45.50" and money(5) == "0.05"
    assert kopecks("80000.00") == 8_000_000 and kopecks("45.5") == 4_550


def test_request_body_without_and_with_receipt() -> None:
    request = PaymentRequest(key="online:abc", amount=4_500_050, currency="RUB", description="Заказ №100001 · " + "к" * 200,
                             return_url="https://t.me/akva_bot", metadata={"order_number": "100001"})
    body = request_body(request)
    assert body["amount"] == {"value": "45000.50", "currency": "RUB"} and body["capture"] is True
    assert body["confirmation"] == {"type": "redirect", "return_url": "https://t.me/akva_bot"}
    assert len(body["description"]) == 128 and body["metadata"] == {"order_number": "100001"}
    assert "receipt" not in body
    receipt = Receipt(email="petrov@mail.ru", item="Участие в курсе", vat_code=1, payment_mode="full_prepayment",
                      payment_subject="service")
    body = request_body(replace(request, receipt=receipt))
    # Сумма позиций чека обязана совпасть с суммой платежа.
    assert body["receipt"] == {
        "customer": {"email": "petrov@mail.ru"},
        "items": [{"description": "Участие в курсе", "quantity": 1, "amount": {"value": "45000.50", "currency": "RUB"},
                   "vat_code": 1, "payment_mode": "full_prepayment", "payment_subject": "service"}],
    }
    body = request_body(replace(request, receipt=replace(receipt, tax_system_code=2)))
    assert body["receipt"]["tax_system_code"] == 2


def test_parse_payment() -> None:
    paid = parse_payment({"id": "yk-1", "status": "succeeded", "amount": {"value": "80000.00", "currency": "RUB"},
                          "payment_method": {"type": "sbp"}, "test": True})
    assert (paid.status, paid.amount, paid.method_type, paid.test) == (GatewayStatus.SUCCEEDED, 8_000_000, "sbp", True)
    new = parse_payment({"id": "yk-2", "status": "pending", "amount": {"value": "10.00", "currency": "RUB"},
                         "confirmation": {"type": "redirect", "confirmation_url": "https://pay.example/yk-2"}})
    assert new.confirmation_url == "https://pay.example/yk-2" and new.cancel_reason is None and not new.test
    cancelled = parse_payment({"id": "yk-3", "status": "canceled", "amount": {"value": "10.00", "currency": "RUB"},
                               "cancellation_details": {"party": "yoo_money", "reason": "expired_on_confirmation"}})
    assert cancelled.cancel_reason == "expired_on_confirmation"
    with pytest.raises(GatewayError):
        parse_payment({"id": "yk-4", "status": "strange", "amount": {"value": "1.00", "currency": "RUB"}})


# --- Настройки ------------------------------------------------------------------------------------------------------

def test_create_gateway_by_settings() -> None:
    assert create_gateway(_settings()) is None  # по умолчанию off
    assert isinstance(create_gateway(_settings(online_payments="fake")), FakeGateway)
    with pytest.raises(GatewayConfigError, match="локальной"):
        create_gateway(_settings(online_payments="fake", db_host="db.amvera.ru"))
    with pytest.raises(GatewayConfigError, match="APPLICATIONS_ENABLED"):
        create_gateway(_settings(online_payments="fake", applications_enabled=False))
    with pytest.raises(GatewayConfigError, match="YOOKASSA_SHOP_ID"):
        create_gateway(_settings(online_payments="yookassa", yookassa_shop_id=""))
    keys = dict(online_payments="yookassa", yookassa_shop_id="123456", yookassa_secret_key="test_secret")
    gateway = create_gateway(_settings(**keys))
    assert isinstance(gateway, YooKassaGateway)
    # HTTP Basic «shopId:ключ» (base64 от «123456:test_secret»).
    assert gateway._headers == {"Authorization": "Basic MTIzNDU2OnRlc3Rfc2VjcmV0"}
    with pytest.raises(GatewayConfigError, match="YOOKASSA_VAT_CODE"):
        create_gateway(_settings(**keys, yookassa_receipts=True, yookassa_vat_code=""))
    with pytest.raises(GatewayConfigError, match="YOOKASSA_PAYMENT_MODE"):
        create_gateway(_settings(**keys, yookassa_receipts=True, yookassa_vat_code=1,
                                 yookassa_payment_mode="partial_prepayment"))
    assert isinstance(create_gateway(_settings(**keys, yookassa_receipts=True, yookassa_vat_code=11,
                                               yookassa_tax_system_code=2)), YooKassaGateway)


def test_settings_flags_and_receipt() -> None:
    assert not _settings().online_payments_enabled and _settings(online_payments="fake").online_payments_enabled
    assert _settings(online_payments="yookassa").online_payments is Mode.YOOKASSA
    with_receipts = _settings(online_payments="yookassa", yookassa_receipts=True, yookassa_vat_code=1)
    assert receipts_enabled(with_receipts) and not receipts_enabled(_settings(online_payments="yookassa"))
    assert not receipts_enabled(_settings(online_payments="fake", yookassa_receipts=True, yookassa_vat_code=1))
    receipt = build_receipt(with_receipts, "petrov@mail.ru", "Курс")
    assert (receipt.email, receipt.vat_code, receipt.payment_mode, receipt.payment_subject, receipt.tax_system_code) == (
        "petrov@mail.ru", 1, "full_prepayment", "service", None)
    assert build_receipt(_settings(online_payments="yookassa"), "petrov@mail.ru", "Курс") is None


# --- Тестовая оплата -------------------------------------------------------------------------------------------------

def test_fake_gateway() -> None:
    gateway = FakeGateway()
    request = PaymentRequest(key="k1", amount=100, currency="RUB", description="d", return_url="https://t.me/b")
    first = _run(gateway.create(request))
    assert _run(gateway.create(request)).id == first.id  # тот же ключ - тот же платёж
    assert first.status is GatewayStatus.PENDING and first.confirmation_url is None and not gateway.has_payment_page
    assert gateway.finish(first.id, success=True) and not gateway.finish(first.id, success=False)
    assert _run(gateway.get(first.id)).status is GatewayStatus.SUCCEEDED
    lost = _run(gateway.get("fake-unknown"))  # бот перезапускали
    assert (lost.status, lost.cancel_reason) == (GatewayStatus.CANCELED, RESTARTED)


# --- Сервис: выдача ссылки -------------------------------------------------------------------------------------------

class _Repo:
    def __init__(self, order, paid: int = 0, pending=None, payments=()) -> None:
        self.order, self.paid, self.pending = order, paid, pending
        self.payments = {p.id: p for p in payments}
        self.added: list = []
        self.locks: list = []

    async def lock(self, order_id):
        self.locks.append(("order", order_id))
        return self.order if order_id == self.order.id else None

    async def get(self, order_id):
        return self.order if order_id == self.order.id else None

    async def paid_amounts(self, order_ids):
        return {order_id: self.paid for order_id in order_ids} if self.paid else {}

    async def session_for_card(self, session_id):
        return SESSION

    async def client(self, client_id):
        return PAYER

    async def pending_online(self, order_id):
        return self.pending

    def add_payment(self, payment) -> None:
        self.added.append(payment)

    async def get_payment(self, payment_id):
        return self.payments.get(payment_id)

    async def lock_payment(self, payment_id):
        self.locks.append(("payment", payment_id))
        return self.payments.get(payment_id)

    async def pending_payments(self, provider):
        return [p for p in self.payments.values() if p.state is PaymentState.PENDING and p.provider is provider]


class _AdminOrders:
    def __init__(self) -> None:
        self.calls: list = []

    async def after_payment(self, order, amount, paid, now):
        self.calls.append((amount, paid))
        order.status = OrderStatus.PAID if paid >= order.total_amount else OrderStatus.PARTIALLY_PAID
        return SimpleNamespace(paid=paid)


class _Db:
    async def flush(self) -> None:
        return None


def _service(repo: _Repo, provider: PaymentProvider = PaymentProvider.YOOKASSA) -> OnlinePaymentService:
    service = OnlinePaymentService.__new__(OnlinePaymentService)
    service.db, service.provider, service.orders, service.admin_orders = _Db(), provider, repo, _AdminOrders()
    return service


def test_prepare_refuses_what_cannot_be_paid_online() -> None:
    prepare = lambda service, **kw: _run(service.prepare(kw.get("client", PAYER.id), kw.get("email", PAYER.email),
                                                         kw.get("order_id", 77), NOW, kw.get("need_email", False)))
    assert prepare(_service(_Repo(_order())), client=999).outcome is StartOutcome.NOT_PAYABLE  # чужой заказ
    assert prepare(_service(_Repo(_order())), order_id=1).outcome is StartOutcome.NOT_PAYABLE
    for status in (OrderStatus.PAID, OrderStatus.CANCELLED):
        assert prepare(_service(_Repo(_order(status=status)))).outcome is StartOutcome.NOT_PAYABLE
    assert prepare(_service(_Repo(_order(total_amount=None)))).outcome is StartOutcome.NO_AMOUNT
    partly = _order(status=OrderStatus.PARTIALLY_PAID)
    assert prepare(_service(_Repo(partly, paid=8_000_000))).outcome is StartOutcome.NOTHING_LEFT
    repo = _Repo(_order())
    assert prepare(_service(repo), email=None, need_email=True).outcome is StartOutcome.NEED_EMAIL
    assert repo.added == []  # платёж не заводим, пока нет почты для чека


def test_prepare_new_payment_for_the_whole_rest() -> None:
    repo = _Repo(_order(status=OrderStatus.PARTIALLY_PAID), paid=2_000_000)
    prepared = _run(_service(repo).prepare(PAYER.id, None, 77, NOW))
    assert prepared.outcome is StartOutcome.READY and not prepared.reused
    assert prepared.title == "Имплантация <базовый>" and repo.locks == [("order", 77)]
    [payment] = repo.added
    assert (payment.amount, payment.state, payment.provider, payment.method) == (
        6_000_000, PaymentState.PENDING, PaymentProvider.YOOKASSA, PaymentMethod.CARD_ONLINE)
    assert payment.idempotency_key.startswith(KEY_PREFIX) and len(payment.idempotency_key) <= 64
    other = _run(_service(_Repo(_order(status=OrderStatus.PARTIALLY_PAID), paid=2_000_000)).prepare(
        PAYER.id, None, 77, NOW))
    assert other.payment.idempotency_key != payment.idempotency_key


def test_prepare_reuses_a_fresh_link_for_the_same_amount() -> None:
    fresh = _payment(created_at=NOW - LINK_TTL + timedelta(minutes=1))
    repo = _Repo(_order(), pending=fresh)
    prepared = _run(_service(repo).prepare(PAYER.id, None, 77, NOW))
    assert prepared.reused and prepared.payment is fresh and repo.added == []
    # Старая ссылка, другая сумма, ещё не созданная у провайдера или другой провайдер - новый платёж.
    for stale in (_payment(created_at=NOW - LINK_TTL), _payment(amount=1), _payment(provider_payment_id=None),
                  _payment(provider=PaymentProvider.FAKE)):
        repo = _Repo(_order(), pending=stale)
        assert not _run(_service(repo).prepare(PAYER.id, None, 77, NOW)).reused and len(repo.added) == 1


# --- Сервис: итог оплаты -----------------------------------------------------------------------------------------

def _remote(status=GatewayStatus.SUCCEEDED, amount=8_000_000, **kw) -> GatewayPayment:
    return GatewayPayment(id="yk-1", status=status, amount=amount, **kw)


def test_apply_success_secures_the_seat_once() -> None:
    payment = _payment()
    repo = _Repo(_order(), payments=[payment])
    service = _service(repo)
    applied = _run(service.apply(5, _remote(method_type="sbp"), NOW))
    # Блокировки - как у отмены и отметки админа: заказ, потом платёж.
    assert repo.locks == [("order", 77), ("payment", 5)]
    assert applied.changed and applied.full and applied.problem is None and applied.paid == 8_000_000
    assert (payment.state, payment.method, payment.paid_at, payment.checked_at) == (
        PaymentState.SUCCEEDED, PaymentMethod.SBP, NOW, NOW)
    assert service.admin_orders.calls == [(8_000_000, 8_000_000)] and repo.order.status is OrderStatus.PAID
    # Вторая проверка (кнопка после фоновой сверки) ничего не меняет и не уведомляет.
    repo.paid = 8_000_000
    again = _run(service.apply(5, _remote(), NOW))
    assert not again.changed and len(service.admin_orders.calls) == 1


def test_apply_waiting_and_cancelled() -> None:
    payment = _payment()
    service = _service(_Repo(_order(), payments=[payment]))
    waiting = _run(service.apply(5, _remote(GatewayStatus.PENDING), NOW))
    assert not waiting.changed and payment.state is PaymentState.PENDING and payment.checked_at == NOW
    cancelled = _run(service.apply(5, _remote(GatewayStatus.CANCELED, cancel_reason="insufficient_funds"), NOW))
    assert cancelled.changed and payment.state is PaymentState.CANCELED and payment.cancel_reason == "insufficient_funds"
    assert service.admin_orders.calls == [] and _run(service.apply(404, _remote(), NOW)) is None


def test_apply_money_for_a_cancelled_or_paid_order() -> None:
    payment = _payment()
    service = _service(_Repo(_order(status=OrderStatus.CANCELLED), payments=[payment]))
    applied = _run(service.apply(5, _remote(), NOW))
    # Деньги получены - платёж записан; заказ не трогаем: он сам попадёт в «Деньги после отмены».
    assert payment.state is PaymentState.SUCCEEDED and applied.problem is Problem.ORDER_CANCELLED
    assert service.admin_orders.calls == [] and service.orders.order.status is OrderStatus.CANCELLED
    payment = _payment()
    service = _service(_Repo(_order(status=OrderStatus.PAID), paid=8_000_000, payments=[payment]))
    applied = _run(service.apply(5, _remote(), NOW))
    assert (applied.problem, applied.extra, applied.paid) == (Problem.OVERPAID, 8_000_000, 16_000_000)
    payment = _payment(amount=6_000_000)
    service = _service(_Repo(_order(status=OrderStatus.PARTIALLY_PAID), paid=2_000_000, payments=[payment]))
    applied = _run(service.apply(5, _remote(amount=6_500_000), NOW))
    assert payment.amount == 6_500_000 and applied.problem is Problem.AMOUNT_CHANGED and applied.extra == 500_000


def test_applied_earlier_keeps_the_warning() -> None:
    # «Проверить оплату» после фоновой сверки: экран всё равно знает, что заказ отменён или оплачен дважды.
    done = _payment(state=PaymentState.SUCCEEDED)
    cancelled = _run(_service(_Repo(_order(status=OrderStatus.CANCELLED), paid=8_000_000)).current(done))
    assert not cancelled.changed and cancelled.problem is Problem.ORDER_CANCELLED
    twice = _run(_service(_Repo(_order(status=OrderStatus.PAID), paid=16_000_000)).current(done))
    assert (twice.problem, twice.extra, twice.full) == (Problem.OVERPAID, 8_000_000, True)
    fine = _run(_service(_Repo(_order(status=OrderStatus.PAID), paid=8_000_000)).current(done))
    assert fine.problem is None and fine.extra == 0
    failed = _run(_service(_Repo(_order(status=OrderStatus.CANCELLED))).current(_payment(state=PaymentState.CANCELED)))
    assert failed.problem is None


def test_due_for_check_and_not_created() -> None:
    lost = _payment(id=1, provider_payment_id=None, created_at=NOW - CREATE_GRACE - timedelta(seconds=1))
    creating = _payment(id=2, provider_payment_id=None, created_at=NOW - timedelta(seconds=30))
    never_checked = _payment(id=3, provider_payment_id="yk-3")
    just_checked = _payment(id=4, provider_payment_id="yk-4", checked_at=NOW - timedelta(seconds=30))
    old = _payment(id=5, provider_payment_id="yk-5", created_at=NOW - timedelta(hours=3),
                   checked_at=NOW - timedelta(minutes=16))
    fake = _payment(id=6, provider=PaymentProvider.FAKE)
    service = _service(_Repo(_order(), payments=[lost, creating, never_checked, just_checked, old, fake]))
    due, closed = _run(service.due_for_check(NOW, limit=20))
    assert due == [(3, "yk-3"), (5, "yk-5")] and closed == 1
    assert (lost.state, lost.cancel_reason) == (PaymentState.CANCELED, NOT_CREATED)
    assert creating.state is PaymentState.PENDING
    assert _run(service.due_for_check(NOW, limit=1))[0] == [(3, "yk-3")]


def test_check_schedule() -> None:
    assert check_interval(timedelta(minutes=10)) == timedelta(minutes=1)
    assert check_interval(timedelta(hours=2)) == timedelta(minutes=15)
    assert check_interval(timedelta(days=2)) == timedelta(hours=6)


# --- Тексты и кнопки ---------------------------------------------------------------------------------------------

def _applied(**kw) -> Applied:
    fields = dict(payment=_payment(state=PaymentState.SUCCEEDED), order=_order(status=OrderStatus.PAID),
                  course_session=SESSION, payer=PAYER, changed=True, paid=8_000_000, method_type="sbp")
    return Applied(**{**fields, **kw})


def test_client_texts() -> None:
    link = render.pay_online(100001, "Имплантация <базовый>", 8_000_000, "petrov@mail.ru", payment_page=True)
    assert "Оплата онлайн</b> · заказ №100001" in link and "Имплантация &lt;базовый&gt;" in link
    assert f"К оплате: {format_rub(8_000_000)}" in link and "Чек придёт на petrov@mail.ru" in link
    assert "Чек придёт" not in render.pay_online(100001, "Курс", 100, None, payment_page=True)
    assert "Тестовая оплата" in render.pay_online(100001, "Курс", 100, None, payment_page=False)
    failed = render.pay_online_failed(100001, "insufficient_funds")
    assert "Оплата не прошла</b> · заказ №100001" in failed and "не хватило средств" in failed
    assert "Деньги не списаны" in render.pay_online_failed(100001, None)
    order = _order()
    individual = render.pay_individual(order, None, TZ, online=True)
    assert "Оплатите онлайн кнопкой ниже" in individual and "до 29 октября, 12:00" in individual
    assert render.pay_individual(order, None, TZ) == texts.PAY_INDIVIDUAL.format(number=100001, email="",
                                                                               due="29 октября, 12:00")


def test_payment_received_after_online_payment() -> None:
    assert texts.PAYMENT_RECEIVED_FULL in render.online_payment_received(_applied())
    cancelled = render.online_payment_received(_applied(order=_order(status=OrderStatus.CANCELLED),
                                                        problem=Problem.ORDER_CANCELLED))
    assert texts.PAYMENT_RECEIVED_AFTER_CANCEL in cancelled and "место закреплено" not in cancelled
    overpaid = render.online_payment_received(_applied(paid=16_000_000, extra=8_000_000, problem=Problem.OVERPAID))
    assert f"лишние {format_rub(8_000_000)}" in overpaid


def test_admin_notice_about_online_payment() -> None:
    text, markup = admin_orders_views.notify_online_paid(_applied(), TZ)
    assert "Онлайн-оплата</b> · заказ №100001" in text and "Петров Пётр Петрович · +79990000010" in text
    assert f"{format_rub(8_000_000)} (СБП) - заказ оплачен полностью" in text and "⚠️" not in text
    assert _rows(markup) == [[texts.BTN_ADMIN_OPEN_ORDER]]
    partial = admin_orders_views.notify_online_paid(_applied(paid=2_000_000, method_type="bank_card", test=True), TZ)[0]
    assert f"(картой) - оплачено {format_rub(2_000_000)} из {format_rub(8_000_000)}" in partial
    assert texts.ADMIN_ONLINE_TEST in partial
    late = admin_orders_views.notify_online_paid(_applied(problem=Problem.ORDER_CANCELLED, method_type=None), TZ)[0]
    assert "(онлайн) - заказ отменён" in late and "Заказ уже отменён" in late
    over = admin_orders_views.notify_online_paid(_applied(problem=Problem.OVERPAID, extra=8_000_000), TZ)[0]
    assert f"Переплата {format_rub(8_000_000)}" in over


def test_admin_card_shows_the_issued_link() -> None:
    link = SimpleNamespace(amount=6_000_000, created_at=NOW)
    card = AdminOrderCard(_order(), SESSION, None, PAYER, [], 2_000_000, 0, False, online_pending=link)
    text, _ = admin_orders_views.order_card(card, TZ, NOW)
    assert f"Ссылка на онлайн-оплату {format_rub(6_000_000)} выдана 26 октября, 12:00" in text
    ask, _ = admin_orders_views.ask_amount(card)
    assert "будет переплата" in ask
    plain = AdminOrderCard(_order(), SESSION, None, PAYER, [], 0, 0, False)
    assert "онлайн-оплату" not in admin_orders_views.order_card(plain, TZ, NOW)[0]
    assert "переплата" not in admin_orders_views.ask_amount(plain)[0]


def test_pay_buttons() -> None:
    markup = keyboards.pay_online(5, "https://pay.example/yk-1", index=2)
    assert _rows(markup) == [[texts.BTN_PAY_ONLINE_GO], [texts.BTN_PAY_ONLINE_CHECK], [texts.BTN_TO_MY_APPLICATIONS]]
    assert markup.inline_keyboard[0][0].url == "https://pay.example/yk-1"
    check = PayCb.unpack(markup.inline_keyboard[1][0].callback_data)
    assert (check.action, check.item_id, check.index) == (PayAction.CHECK, 5, 2)
    fake = keyboards.pay_online(5, None, index=0, payment_page=False)
    assert _rows(fake)[0] == [texts.BTN_PAY_FAKE_OK, texts.BTN_PAY_FAKE_FAIL]
    # Платёж ещё создаётся (ссылки нет) - только «Проверить», без тестовых кнопок.
    assert _rows(keyboards.pay_online(5, None, index=0)) == [[texts.BTN_PAY_ONLINE_CHECK],
                                                             [texts.BTN_TO_MY_APPLICATIONS]]
    start = PayCb.unpack(keyboards.pay_individual(77).inline_keyboard[0][0].callback_data)
    assert (start.action, start.item_id) == (PayAction.START, 77)
    assert _rows(keyboards.pay_online_retry(77, 1)) == [[texts.BTN_PAY_ONLINE_RETRY], [texts.BTN_TO_MY_APPLICATIONS]]


def test_online_button_in_my_applications_and_reminder() -> None:
    def card(order, paid=0, online=True):
        app = SimpleNamespace(enrollment=SimpleNamespace(id=1, status=None), order=order, active=True, is_payer=True,
                              self_cancel=False, paid=paid)
        return [row for row in _rows(keyboards.my_application_card(app, 0, 1, online=online))]

    assert [texts.BTN_PAY_ONLINE] in card(_order())
    assert [texts.BTN_PAY_ONLINE] not in card(_order(), online=False)
    assert [texts.BTN_PAY_ONLINE] not in card(_order(payer_type=PayerType.ORGANIZATION, requisites_file_id="F"))
    assert [texts.BTN_PAY_ONLINE] not in card(_order(total_amount=None))
    assert [texts.BTN_PAY_ONLINE] not in card(_order(status=OrderStatus.PARTIALLY_PAID), paid=8_000_000)
    assert [texts.BTN_ORDER_PAY] in card(_order(payer_type=None))  # сначала «Физлицо / Юрлицо»
    reminder = keyboards.payment_reminder(77, choose_payer=False, manager_url="https://t.me/m", online=True)
    assert _rows(reminder) == [[texts.BTN_PAY_ONLINE], [texts.BTN_CANCEL_VIA_MANAGER]]
    assert keyboards.payment_reminder(77, False, None) is None


def test_reminder_letter_gets_the_online_button() -> None:
    from stubbot.services.reminders import ReminderJob, ReminderKind
    from stubbot.tg.reminders import letters

    settings = SimpleNamespace(manager_url=None, timezone=TZ, admin_chat_ids=[1], online_payments_enabled=True)
    job = ReminderJob(ReminderKind.DAY, _order(), SESSION, PAYER, 1)
    assert _rows(letters(job, settings)[0].markup) == [[texts.BTN_PAY_ONLINE]]
    organization = ReminderJob(ReminderKind.DAY, _order(payer_type=PayerType.ORGANIZATION), SESSION, PAYER, 1)
    assert letters(organization, settings)[0].markup is None


# --- Координатор: порядок шагов, письма, фоновая сверка ----------------------------------------------------------

class _Factory:
    """async with factory() as session, session.begin(): - записывает начало и конец транзакции."""

    def __init__(self, log: list) -> None:
        self.log = log

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def begin(self):
        log = self.log

        class _Tx:
            async def __aenter__(self):
                log.append("begin")

            async def __aexit__(self, *exc):
                log.append("commit")

        return _Tx()


class _Bot:
    def __init__(self, log: list) -> None:
        self.log = log

    async def me(self):
        return SimpleNamespace(username="akva_bot")

    async def send_message(self, chat_id, text, reply_markup=None):
        self.log.append(("send", chat_id))


class _Gateway:
    provider = PaymentProvider.YOOKASSA
    has_payment_page = True

    def __init__(self, log: list, errors=(), remote: GatewayPayment | None = None) -> None:
        self.log, self.errors, self.remote = log, list(errors), remote
        self.requests: list[PaymentRequest] = []

    async def create(self, request):
        self.requests.append(request)
        self.log.append("create")
        if self.errors:
            raise self.errors.pop(0)
        return GatewayPayment("yk-1", GatewayStatus.PENDING, request.amount, "https://pay.example/yk-1")

    async def get(self, payment_id):
        self.log.append(("get", payment_id))
        return self.remote

    async def close(self) -> None:
        return None


class _FakeService:
    """Сервис для координатора: что вызвали и в каком порядке."""

    def __init__(self, log: list, prepared: Prepared | None = None, found=None, applied: Applied | None = None,
                 due=()) -> None:
        self.log, self.prepared, self.found, self.applied, self.due = log, prepared, found, applied, list(due)

    async def prepare(self, client_id, email, order_id, now, need_email=False):
        self.log.append("prepare")
        return self.prepared

    async def attach(self, payment_id, remote, now):
        self.log.append(("attach", payment_id, remote.id))

    async def give_up(self, payment_id, reason, now):
        self.log.append(("give_up", payment_id, reason))

    async def find(self, payment_id, client_id=None):
        self.log.append(("find", payment_id, client_id))
        return self.found

    async def current(self, payment):
        return self.applied

    async def apply(self, payment_id, remote, now):
        self.log.append(("apply", payment_id))
        return self.applied

    async def touch(self, payment_id, now):
        self.log.append(("touch", payment_id))

    async def due_for_check(self, now, limit):
        return self.due, 0


def _coordinator(monkeypatch, log: list, service: _FakeService, gateway, **settings) -> OnlinePayments:
    monkeypatch.setattr(coordinator_module, "OnlinePaymentService", lambda session, provider: service)
    monkeypatch.setattr(coordinator_module, "RETRY_PAUSE", 0)

    async def notify_admins(bot, settings, text, markup=None, document=None, photo=None):
        log.append(("admins", text.splitlines()[0]))

    monkeypatch.setattr(coordinator_module, "notify_admins", notify_admins)
    return OnlinePayments(_Bot(log), _Factory(log), _settings(online_payments="yookassa", yookassa_shop_id="1",
                                                              yookassa_secret_key="k", **settings), gateway, interval=0)


def _prepared(**kw) -> Prepared:
    fields = dict(outcome=StartOutcome.READY, order=_order(), payment=_payment(provider_payment_id=None,
                                                                               confirmation_url=None),
                  title="Имплантация")
    return Prepared(**{**fields, **kw})


def test_start_creates_the_payment_after_the_row_is_committed(monkeypatch) -> None:
    log: list = []
    gateway = _Gateway(log)
    payments = _coordinator(monkeypatch, log, _FakeService(log, _prepared()), gateway)
    started = _run(payments.start(PAYER.id, None, 77, NOW))
    # Запрос к ЮKassa - между транзакциями: строка с ключом уже в базе, блокировки заказа нет.
    assert log == ["begin", "prepare", "commit", "create", "begin", ("attach", 5, "yk-1"), "commit"]
    assert (started.outcome, started.url, started.amount, started.order_number) == (
        StartOutcome.READY, "https://pay.example/yk-1", 8_000_000, 100001)
    [request] = gateway.requests
    assert (request.key, request.return_url, request.receipt) == ("online:abc", "https://t.me/akva_bot", None)
    assert request.description == "Заказ №100001 · Имплантация" and request.metadata["order_number"] == "100001"


def test_start_with_receipt_and_retry(monkeypatch) -> None:
    log: list = []
    gateway = _Gateway(log, errors=[GatewayError("нет ответа", retryable=True)])
    payments = _coordinator(monkeypatch, log, _FakeService(log, _prepared()), gateway, yookassa_receipts=True,
                            yookassa_vat_code=1)
    started = _run(payments.start(PAYER.id, "petrov@mail.ru", 77, NOW))
    assert started.outcome is StartOutcome.READY and log.count("create") == 2
    first, second = gateway.requests
    assert first.key == second.key  # повтор с тем же ключом - ЮKassa не создаст второй платёж
    assert first.receipt.email == "petrov@mail.ru" and "Имплантация" in first.receipt.item


def test_start_failure_closes_the_row(monkeypatch) -> None:
    log: list = []
    gateway = _Gateway(log, errors=[GatewayError("ошибка 401 invalid_credentials")])
    payments = _coordinator(monkeypatch, log, _FakeService(log, _prepared()), gateway)
    started = _run(payments.start(PAYER.id, None, 77, NOW))
    assert started.outcome is StartOutcome.ERROR and started.url is None and log.count("create") == 1
    assert ("give_up", 5, NOT_CREATED) in log


def test_start_reuses_the_link_and_passes_refusals(monkeypatch) -> None:
    log: list = []
    reused = _prepared(payment=_payment(), reused=True)
    started = _run(_coordinator(monkeypatch, log, _FakeService(log, reused), _Gateway(log)).start(PAYER.id, None, 77))
    assert started.url == "https://pay.example/yk-1" and "create" not in log
    refused = Prepared(StartOutcome.NO_AMOUNT, _order(total_amount=None))
    started = _run(_coordinator(monkeypatch, [], _FakeService([], refused), _Gateway([])).start(PAYER.id, None, 77))
    assert (started.outcome, started.order_number) == (StartOutcome.NO_AMOUNT, 100001)


def test_check_by_button_tells_only_admins(monkeypatch) -> None:
    log: list = []
    service = _FakeService(log, found=_payment(), applied=_applied())
    payments = _coordinator(monkeypatch, log, service, _Gateway(log, remote=_remote()))
    applied = _run(payments.check(5, client_id=PAYER.id, now=NOW))
    assert applied.payment.state is PaymentState.SUCCEEDED
    assert ("find", 5, PAYER.id) in log and ("get", "yk-1") in log and ("apply", 5) in log
    # Клиент видит итог на экране - письма ему нет; админам - уведомление.
    assert ("send", PAYER.telegram_id) not in log and ("admins", "💳 <b>Онлайн-оплата</b> · заказ №100001") in log


def test_background_tick_tells_the_client_once(monkeypatch) -> None:
    log: list = []
    service = _FakeService(log, applied=_applied(), due=[(5, "yk-1")])
    payments = _coordinator(monkeypatch, log, service, _Gateway(log, remote=_remote()))
    assert _run(payments.tick(NOW)) == 1
    assert log.index(("apply", 5)) < log.index(("send", PAYER.telegram_id))  # письма - после записи оплаты
    assert sum(1 for entry in log if entry[0] == "admins") == 1
    log.clear()
    service.applied = _applied(changed=False)  # итог уже применён кнопкой - повторных писем нет
    _run(payments.tick(NOW))
    assert ("send", PAYER.telegram_id) not in log and not any(entry[0] == "admins" for entry in log)


def test_check_survives_a_provider_error(monkeypatch) -> None:
    log: list = []

    class _Down(_Gateway):
        async def get(self, payment_id):
            raise GatewayError("нет ответа", retryable=True)

    pending = _applied(payment=_payment(), changed=False)
    service = _FakeService(log, found=_payment(), applied=pending)
    applied = _run(_coordinator(monkeypatch, log, service, _Down(log)).check(5, client_id=PAYER.id, now=NOW))
    assert applied is pending and ("touch", 5) in log and ("apply", 5) not in log


def test_check_of_someone_elses_payment(monkeypatch) -> None:
    log: list = []
    payments = _coordinator(monkeypatch, log, _FakeService(log, found=None), _Gateway(log))
    assert _run(payments.check(5, client_id=999, now=NOW)) is None and ("get", "yk-1") not in log


def test_fake_finish_only_with_fake_gateway(monkeypatch) -> None:
    log: list = []
    payments = _coordinator(monkeypatch, log, _FakeService(log, found=_payment()), _Gateway(log))
    assert _run(payments.finish_fake(5, PAYER.id, success=True)) is None  # ЮKassa - кнопки тестовой оплаты не работают
    gateway = FakeGateway()
    request = PaymentRequest(key="k", amount=100, currency="RUB", description="d", return_url="https://t.me/b")
    remote = _run(gateway.create(request))
    service = _FakeService(log, found=_payment(provider_payment_id=remote.id, provider=PaymentProvider.FAKE),
                           applied=_applied())
    fake = _coordinator(monkeypatch, log, service, gateway)
    assert _run(fake.finish_fake(5, PAYER.id, success=True, now=NOW)) is not None
    assert _run(gateway.get(remote.id)).status is GatewayStatus.SUCCEEDED


def test_service_module_has_no_telegram() -> None:
    # Сервис решает про деньги и не знает про Telegram: письма - только в tg/online_payments.py.
    assert "aiogram" not in open(service_module.__file__, encoding="utf-8").read()
