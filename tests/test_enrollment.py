"""Запись на курс (полная версия, этап 1, шаг 2): правила записи, заказ, коллега, экраны. Без БД и Telegram."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from stubbot.db.enums import EnrollmentStatus, OrderStatus, PayerType, PriceUnit, SessionStatus
from stubbot.services.enrollments import Colleague, EnrollmentService, EnrollOutcome
from stubbot.services.orders import MAX_FILE_NAME, OrderService
from stubbot.services.schedule import SessionCard
from stubbot.tg import keyboards, render, texts
from stubbot.tg.callbacks import ApplyAction, ApplyCb, OrderAction, OrderCb
from stubbot.utils.money import format_rub

TODAY = date(2026, 10, 20)
NOW = datetime(2026, 10, 20, 11, 30, tzinfo=UTC)
FULL = SimpleNamespace(id=1, amount=4_500_000, currency="RUB", unit=PriceUnit.PER_PERSON, min_seats=1, max_seats=None,
                       is_active=True, label="Полный курс")
PAIR = SimpleNamespace(id=2, amount=8_000_000, currency="RUB", unit=PriceUnit.PER_GROUP, min_seats=2, max_seats=2,
                       is_active=True, label="Для двух коллег")
ME = SimpleNamespace(id=10, phone="+79990000010", full_name="Петров Пётр Петрович", referral_code="abc")


def _session(prices=(FULL, PAIR), status=SessionStatus.REGISTRATION_OPEN) -> SimpleNamespace:
    return SimpleNamespace(id=5, status=status, price_options=list(prices), registration_deadline=None)


class _Schedule:
    def __init__(self, card: SessionCard | None) -> None:
        self._card = card

    async def card(self, session_id: int, today: date) -> SessionCard | None:
        return self._card


class _Enrollments:
    def __init__(self, active: dict[int, object] | None = None) -> None:
        self.active = active or {}
        self.added: list = []
        self.locked: list[int] = []

    async def lock_session(self, session_id: int) -> None:
        self.locked.append(session_id)

    async def active_for(self, client_id: int, session_id: int):
        return self.active.get(client_id)

    def add(self, enrollment) -> None:
        self.added.append(enrollment)


class _Orders:
    def __init__(self) -> None:
        self.added: list = []

    async def add(self, order):
        order.id, order.number, order.status = 77, 100001 + len(self.added), OrderStatus.AWAITING_PAYMENT
        self.added.append(order)
        return order


class _Clients:
    def __init__(self, by_phone: dict[str, SimpleNamespace] | None = None) -> None:
        self.by_phone = by_phone or {}
        self.created: list = []

    async def find_by_phone(self, phone: str):
        return self.by_phone.get(phone)

    async def add_offline(self, full_name: str, phone: str):
        client = SimpleNamespace(id=50, full_name=full_name, phone=phone, telegram_id=None)
        self.created.append(client)
        return client


class _Db:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    @asynccontextmanager
    async def begin_nested(self):
        yield

    async def flush(self) -> None:
        if self.fail:
            raise IntegrityError("INSERT", {}, Exception("uq_enrollments_client_id_session_id_active"))


def _service(card: SessionCard | None, enrollments: _Enrollments | None = None, clients: _Clients | None = None,
             fail: bool = False) -> EnrollmentService:
    service = EnrollmentService.__new__(EnrollmentService)  # без БД: только правила
    service.db = _Db(fail)
    service.schedule = _Schedule(card)
    service.enrollments = enrollments or _Enrollments()
    service.orders = _Orders()
    service.clients = clients or _Clients()
    service.analytics = SimpleNamespace(add_event=lambda *a, **k: None)
    return service


def _enroll(service: EnrollmentService, price_id: int | None = 1, colleague: Colleague | None = None):
    return asyncio.run(service.enroll(ME, 5, price_id, colleague, TODAY, NOW))


COLLEAGUE = Colleague("Иванова Анна Сергеевна", "+79991234567")


def test_solo_enrollment_takes_a_seat_and_creates_an_order() -> None:
    service = _service(SessionCard(_session(), seats_left=3))
    result = _enroll(service)
    assert result.outcome is EnrollOutcome.CREATED
    order = result.order
    # Цена закрепляется в момент записи; оплатить - за 72 часа.
    assert (order.payer_client_id, order.session_id, order.price_option_id, order.total_amount) == (10, 5, 1, 4_500_000)
    assert order.due_at == NOW + timedelta(hours=72) and order.number == 100001
    [enrollment] = service.enrollments.added
    assert (enrollment.client_id, enrollment.order_id, enrollment.status) == (10, 77, EnrollmentStatus.APPLICATION)
    assert service.enrollments.locked == [5]  # места считаются под блокировкой проведения


def test_pair_enrolls_a_new_colleague_on_the_same_order() -> None:
    service = _service(SessionCard(_session(), seats_left=2))
    result = _enroll(service, price_id=2, colleague=COLLEAGUE)
    assert result.outcome is EnrollOutcome.CREATED and result.order.total_amount == 8_000_000  # цена за двоих
    [colleague] = service.clients.created
    assert (colleague.full_name, colleague.phone, colleague.telegram_id) == (COLLEAGUE.full_name, COLLEAGUE.phone, None)
    assert [(e.client_id, e.order_id) for e in service.enrollments.added] == [(10, 77), (50, 77)]
    assert [e.enrolled_by_client_id for e in service.enrollments.added] == [None, 10]  # коллегу записал плательщик
    assert result.colleague is colleague


def test_pair_uses_existing_client_with_that_phone() -> None:
    known = SimpleNamespace(id=7, full_name="Иванова А. С.", phone=COLLEAGUE.phone, telegram_id=555)
    service = _service(SessionCard(_session(), seats_left=None), clients=_Clients({COLLEAGUE.phone: known}))
    result = _enroll(service, price_id=2, colleague=COLLEAGUE)
    assert result.outcome is EnrollOutcome.CREATED and result.colleague is known
    assert not service.clients.created and [e.client_id for e in service.enrollments.added] == [10, 7]


def test_pair_with_one_seat_left_goes_to_waitlist_together() -> None:
    service = _service(SessionCard(_session(), seats_left=1))
    result = _enroll(service, price_id=2, colleague=COLLEAGUE)
    assert result.outcome is EnrollOutcome.WAITLIST and result.order is None and not service.orders.added
    assert [(e.status, e.order_id) for e in service.enrollments.added] == [(EnrollmentStatus.WAITLIST, None)] * 2
    # Заказа нет - пару связывает «кто записал»: выйти из листа ожидания они смогут только вместе.
    assert [e.enrolled_by_client_id for e in service.enrollments.added] == [None, 10]


def test_full_session_puts_solo_to_waitlist_without_order() -> None:
    service = _service(SessionCard(_session(status=SessionStatus.FULL), seats_left=0))
    assert _enroll(service).outcome is EnrollOutcome.WAITLIST and not service.orders.added


def test_colleague_checks() -> None:
    card = SessionCard(_session(), seats_left=5)
    own = Colleague("Петров Пётр Петрович", ME.phone)
    assert _enroll(_service(card), price_id=2, colleague=own).outcome is EnrollOutcome.COLLEAGUE_IS_SELF
    busy = SimpleNamespace(id=7, phone=COLLEAGUE.phone, telegram_id=None, full_name=None)
    service = _service(card, enrollments=_Enrollments({7: object()}), clients=_Clients({COLLEAGUE.phone: busy}))
    assert _enroll(service, price_id=2, colleague=COLLEAGUE).outcome is EnrollOutcome.COLLEAGUE_DUPLICATE
    assert not service.orders.added


def test_tariff_and_colleague_must_match() -> None:
    card = SessionCard(_session(), seats_left=5)
    assert _enroll(_service(card), price_id=2).outcome is EnrollOutcome.BAD_PRICE  # вдвоём, а коллеги нет
    assert _enroll(_service(card), price_id=1, colleague=COLLEAGUE).outcome is EnrollOutcome.BAD_PRICE
    assert _enroll(_service(card), price_id=99).outcome is EnrollOutcome.BAD_PRICE  # тариф не этого проведения
    assert _enroll(_service(card), price_id=None).outcome is EnrollOutcome.BAD_PRICE  # тарифы есть - выбрать надо


def test_session_without_prices_creates_order_with_open_amount() -> None:
    service = _service(SessionCard(_session(prices=()), seats_left=None))
    result = _enroll(service, price_id=None)
    assert result.outcome is EnrollOutcome.CREATED and result.order.total_amount is None  # «стоимость уточняется»


def test_duplicate_closed_and_race() -> None:
    card = SessionCard(_session(), seats_left=5)
    existing = SimpleNamespace(status=EnrollmentStatus.APPLICATION)
    assert _enroll(_service(card, enrollments=_Enrollments({10: existing}))).outcome is EnrollOutcome.DUPLICATE
    assert _enroll(_service(None)).outcome is EnrollOutcome.CLOSED
    # Параллельная запись упёрлась в уникальность: своя запись уже есть - «уже записаны», иначе - «ещё раз».
    assert _enroll(_service(card, fail=True)).outcome is EnrollOutcome.BUSY


# --- Заказ со стороны клиента --------------------------------------------------------------------------------------

class _OrderRepo:
    def __init__(self, order) -> None:
        self.order = order

    async def get_for_payer(self, order_id: int, client_id: int):
        return self.order if (order_id, client_id) == (self.order.id, self.order.payer_client_id) else None


def _order(**kw) -> SimpleNamespace:
    fields = dict(id=77, number=100001, payer_client_id=10, status=OrderStatus.AWAITING_PAYMENT, payer_type=None,
                  requisites_file_id=None, requisites_file_name=None, requisites_received_at=None,
                  total_amount=8_000_000, due_at=NOW + timedelta(hours=72))
    return SimpleNamespace(**{**fields, **kw})


def _orders(order) -> OrderService:
    service = OrderService.__new__(OrderService)
    service.orders = _OrderRepo(order)
    return service


def test_only_the_payer_chooses_how_to_pay() -> None:
    order = _order()
    assert asyncio.run(_orders(order).choose_payer_type(99, 77, PayerType.INDIVIDUAL)) is None  # чужой заказ
    assert asyncio.run(_orders(order).choose_payer_type(10, 77, PayerType.INDIVIDUAL)) is order
    assert order.payer_type is PayerType.INDIVIDUAL
    cancelled = _order(status=OrderStatus.CANCELLED)
    assert asyncio.run(_orders(cancelled).choose_payer_type(10, 77, PayerType.ORGANIZATION)) is None


def test_requisites_file_is_saved_to_the_order() -> None:
    order = _order()
    saved = asyncio.run(_orders(order).save_requisites(10, 77, "FILE", "карточка.pdf" + "x" * 300, NOW))
    assert saved is order and order.payer_type is PayerType.ORGANIZATION
    assert (order.requisites_file_id, order.requisites_received_at) == ("FILE", NOW)
    assert len(order.requisites_file_name) == MAX_FILE_NAME


# --- Экраны --------------------------------------------------------------------------------------------------------

def _rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def test_confirm_buttons_with_colleague_checkbox() -> None:
    off, on = texts.CONSENT_MARK_OFF, texts.CONSENT_MARK_ON
    assert _rows(keyboards.apply_confirm()) == [[texts.BTN_APPLY_SEND], [texts.BTN_APPLY_CANCEL]]
    assert _rows(keyboards.apply_confirm(pair=True)) == [[off + texts.BTN_APPLY_COLLEAGUE_OK], [texts.BTN_APPLY_SEND],
                                                         [texts.BTN_APPLY_CANCEL]]
    markup = keyboards.apply_confirm(pair=True, colleague_ok=True)
    assert markup.inline_keyboard[0][0].text == on + texts.BTN_APPLY_COLLEAGUE_OK
    assert markup.inline_keyboard[0][0].callback_data == ApplyCb(action=ApplyAction.COLLEAGUE_OK).pack()
    assert _rows(keyboards.apply_input_cancel()) == [[texts.BTN_APPLY_CANCEL]]


def test_payer_choice_buttons_carry_the_order() -> None:
    markup = keyboards.payer_choice(77)
    assert _rows(markup) == [[texts.BTN_PAY_INDIVIDUAL], [texts.BTN_PAY_ORGANIZATION]]
    assert [row[0].callback_data for row in markup.inline_keyboard] == [
        OrderCb(action=OrderAction.INDIVIDUAL, order_id=77).pack(),
        OrderCb(action=OrderAction.ORGANIZATION, order_id=77).pack()]


def _card() -> SessionCard:
    program = SimpleNamespace(title="Имплантация <базовый>")
    session = SimpleNamespace(title_override=None, program=program, start_date=date(2026, 11, 13),
                              end_date=date(2026, 11, 14), days=[], status=SessionStatus.REGISTRATION_OPEN,
                              price_options=[], registration_deadline=None)
    return SessionCard(session=session, seats_left=None)


def test_confirm_text_for_pair_and_waitlist() -> None:
    pair = render.apply_confirm(_card(), PAIR, "Иванова <Анна>", "+79991234567", waitlist=False)
    assert "Имплантация &lt;базовый&gt;" in pair and "Иванова &lt;Анна&gt; (+79991234567)" in pair
    assert "за двоих" in pair and texts.APPLY_CONFIRM_NOTE in pair and "«АкваСтом Груп»" in pair
    solo = render.apply_confirm(_card(), FULL, None, None, waitlist=True)
    assert texts.APPLY_CONFIRM_WAITLIST_NOTE in solo and "Участники" not in solo and "АкваСтом" not in solo
    assert render.apply_confirm(_card(), None, None, None, waitlist=False).count(texts.APPLY_NO_PRICE) == 1


def test_enrolled_and_payment_texts() -> None:
    order = _order()
    text = render.enrolled(_card(), order, "Иванова Анна Сергеевна", "Europe/Moscow")
    assert "Заказ №100001" in text and format_rub(8_000_000) in text
    assert "23 октября, 14:30" in text  # 72 часа от 11:30 UTC 20.10 - по Москве
    assert "Вместе с Вами: Иванова Анна Сергеевна" in text
    assert render.order_amount(_order(total_amount=None)) == texts.APPLY_NO_PRICE
    assert "name@mail.ru" in render.pay_individual(order, "name@mail.ru", "Europe/Moscow")
    assert "почту" not in render.pay_individual(order, None, "Europe/Moscow")
    note = render.colleague_enrolled(_card(), "Петров <П. П.>")
    assert "Петров &lt;П. П.&gt;" in note and "Имплантация &lt;базовый&gt;" in note


def test_popups_fit_telegram_limit() -> None:
    for popup in (texts.APPLY_COLLEAGUE_OK_REQUIRED, texts.ORDER_UNAVAILABLE):
        assert len(popup) <= 200 and "<" not in popup
