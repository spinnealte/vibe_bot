"""«Мои записи» и отмена записи самим клиентом (полная версия, этап 1, шаг 3). Без БД и Telegram."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from stubbot.db.enums import CancelChoice, EnrollmentStatus, OrderStatus, PayerType
from stubbot.services.enrollments import (
    CancelOutcome,
    CancelResult,
    EnrollmentService,
    MyApplication,
    can_self_cancel,
    cancel_deadline,
)
from stubbot.tg import keyboards, render, texts
from stubbot.tg.callbacks import MyApplicationAction, MyApplicationCb, OrderAction, OrderCb
from stubbot.utils.money import format_rub

TZ = "Europe/Moscow"
TODAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
START = date(2026, 11, 13)  # отменить самому - до 30 октября включительно
ME = 10
COLLEAGUE = SimpleNamespace(id=50, full_name="Петрова Анна Сергеевна", telegram_id=555)


def _course_session(start: date | None = START) -> SimpleNamespace:
    return SimpleNamespace(id=5, program_id=3, start_date=start, end_date=start and start + timedelta(days=1),
                           days=[], venue=None, title_override=None,
                           program=SimpleNamespace(title="Имплантация <базовый>"))


def _enrollment(**kw) -> SimpleNamespace:
    fields = dict(id=1, client_id=ME, session_id=5, order_id=77, enrolled_by_client_id=None,
                  status=EnrollmentStatus.APPLICATION, cancelled_at=None, cancel_reason=None)
    return SimpleNamespace(**{**fields, **kw})


def _order(**kw) -> SimpleNamespace:
    fields = dict(id=77, number=100001, payer_client_id=ME, status=OrderStatus.AWAITING_PAYMENT, payer_type=None,
                  requisites_file_id=None, total_amount=8_000_000, due_at=NOW + timedelta(hours=72),
                  cancelled_at=None, cancel_reason=None, cancel_choice=None)
    return SimpleNamespace(**{**fields, **kw})


# --- Правила: кто и до какого дня отменяет сам ---------------------------------------------------------------------

def test_self_cancel_until_two_weeks_before_start_inclusive() -> None:
    course_session = _course_session()
    assert cancel_deadline(course_session) == date(2026, 10, 30)
    assert can_self_cancel(_enrollment(), course_session, date(2026, 10, 30))
    assert not can_self_cancel(_enrollment(), course_session, date(2026, 10, 31))  # дальше - через менеджера
    # Оплаченную (место закреплено) - тоже сам, по тому же сроку.
    assert can_self_cancel(_enrollment(status=EnrollmentStatus.CONFIRMED), course_session, TODAY)


def test_session_without_dates_can_always_be_cancelled() -> None:
    course_session = _course_session(start=None)
    assert cancel_deadline(course_session) is None
    assert can_self_cancel(_enrollment(), course_session, date(2027, 1, 1))


def test_colleague_waitlist_and_finished_records() -> None:
    course_session = _course_session()
    # Коллега из пары отказывается через менеджера; пару отменяет только записавший.
    assert not can_self_cancel(_enrollment(enrolled_by_client_id=ME), course_session, TODAY)
    # Из листа ожидания выйти можно всегда - места и денег там нет.
    late = date(2026, 11, 10)
    assert can_self_cancel(_enrollment(status=EnrollmentStatus.WAITLIST, order_id=None), course_session, late)
    for status in (EnrollmentStatus.CANCELLED_BY_ADMIN, EnrollmentStatus.ATTENDED, EnrollmentStatus.CANCELLED_BY_CLIENT):
        assert not can_self_cancel(_enrollment(status=status), course_session, TODAY)


# --- Отмена в сервисе ----------------------------------------------------------------------------------------------

class _Enrollments:
    def __init__(self, enrollment, colleagues=(), course_session=None, calls=None) -> None:
        self.enrollment = enrollment
        self._colleagues = list(colleagues)
        self.course_session = course_session or _course_session()
        self.calls = calls if calls is not None else []
        self.course_waitlist: list[tuple[int, int]] = []

    async def get_for_client(self, enrollment_id: int, client_id: int, lock: bool = False):
        self.calls.append("enrollment+lock" if lock else "enrollment")
        e = self.enrollment
        return e if (enrollment_id, client_id) == (e.id, e.client_id) else None

    async def get_session(self, session_id: int):
        return self.course_session

    async def colleagues(self, session_id: int, enroller_id: int):
        return self._colleagues

    async def put_on_course_waitlist(self, client_id: int, program_id: int) -> None:
        self.course_waitlist.append((client_id, program_id))


class _Orders:
    def __init__(self, order, paid: int = 0, calls=None) -> None:
        self.order, self.paid = order, paid
        self.calls = calls if calls is not None else []

    async def lock(self, order_id: int):
        self.calls.append("order+lock")
        return self.order if self.order is not None and self.order.id == order_id else None

    async def paid_amounts(self, order_ids: list[int]) -> dict[int, int]:
        return {order_id: self.paid for order_id in order_ids} if self.paid else {}


def _service(enrollment, order=None, paid: int = 0, colleagues=(), course_session=None):
    calls: list[str] = []
    service = EnrollmentService.__new__(EnrollmentService)  # без БД: только правила
    service.enrollments = _Enrollments(enrollment, colleagues, course_session, calls)
    service.orders = _Orders(order, paid, calls)
    service.analytics = SimpleNamespace(add_event=lambda *a, **k: None)
    return service, calls


def _cancel(service, choice: CancelChoice | None = None, client_id: int = ME, today: date = TODAY) -> CancelResult:
    return asyncio.run(service.cancel(client_id, 1, today, choice, NOW))


def test_unpaid_cancel_closes_the_order_and_puts_on_course_waitlist() -> None:
    enrollment, order = _enrollment(), _order()
    service, calls = _service(enrollment, order)
    result = _cancel(service, CancelChoice.ADVANCE)  # выбор без оплаты не сохраняется - денег нет
    assert result.outcome is CancelOutcome.CANCELLED and result.choice is None and result.course_waitlist
    assert (enrollment.status, enrollment.cancelled_at) == (EnrollmentStatus.CANCELLED_BY_CLIENT, NOW)
    assert (order.status, order.cancelled_at, order.cancel_choice) == (OrderStatus.CANCELLED, NOW, None)
    assert service.enrollments.course_waitlist == [(ME, 3)]
    # Заказ блокируется раньше записи - тот же порядок у отметки оплаты админом, без взаимной блокировки.
    assert calls == ["enrollment", "order+lock", "enrollment+lock"]


def test_pair_is_cancelled_together_and_both_wait_for_the_course() -> None:
    enrollment, order = _enrollment(), _order()
    colleague_row = _enrollment(id=2, client_id=COLLEAGUE.id, enrolled_by_client_id=ME)
    service, _ = _service(enrollment, order, colleagues=[(colleague_row, COLLEAGUE)])
    result = _cancel(service)
    assert result.outcome is CancelOutcome.CANCELLED and result.colleagues == (COLLEAGUE,)
    assert colleague_row.status is EnrollmentStatus.CANCELLED_BY_CLIENT
    assert service.enrollments.course_waitlist == [(ME, 3), (COLLEAGUE.id, 3)]


def test_paid_cancel_needs_a_choice_first() -> None:
    enrollment, order = _enrollment(status=EnrollmentStatus.CONFIRMED), _order(status=OrderStatus.PARTIALLY_PAID)
    service, _ = _service(enrollment, order, paid=2_000_000)
    result = _cancel(service)
    assert result.outcome is CancelOutcome.NEEDS_CHOICE and result.paid == 2_000_000
    assert enrollment.status is EnrollmentStatus.CONFIRMED and order.status is OrderStatus.PARTIALLY_PAID  # не тронуто


def test_paid_cancel_keeps_the_choice_for_the_admin() -> None:
    for choice in CancelChoice:
        enrollment, order = _enrollment(status=EnrollmentStatus.CONFIRMED), _order(status=OrderStatus.PAID)
        service, _ = _service(enrollment, order, paid=8_000_000)
        result = _cancel(service, choice)
        assert result.outcome is CancelOutcome.CANCELLED and result.choice is choice and not result.course_waitlist
        assert order.status is OrderStatus.CANCELLED and order.cancel_choice is choice
        assert not service.enrollments.course_waitlist  # деньги двигает админ, в лист ожидания курса - нет


def test_cancel_refused_late_for_colleague_and_for_someone_else() -> None:
    late, _ = _service(_enrollment(), _order())
    assert _cancel(late, today=date(2026, 10, 31)).outcome is CancelOutcome.NOT_ALLOWED
    assert late.orders.order.status is OrderStatus.AWAITING_PAYMENT
    colleague, _ = _service(_enrollment(client_id=COLLEAGUE.id, enrolled_by_client_id=ME), _order())
    assert _cancel(colleague, client_id=COLLEAGUE.id).outcome is CancelOutcome.NOT_ALLOWED
    stranger, _ = _service(_enrollment(), _order())
    assert _cancel(stranger, client_id=99).outcome is CancelOutcome.NOT_ALLOWED  # чужая запись из подделанной кнопки


def test_leaving_the_waitlist() -> None:
    enrollment = _enrollment(status=EnrollmentStatus.WAITLIST, order_id=None)
    service, calls = _service(enrollment)
    result = _cancel(service)
    assert result.outcome is CancelOutcome.CANCELLED and result.waitlist and result.order is None
    assert not result.course_waitlist and not service.enrollments.course_waitlist
    assert "order+lock" not in calls


def test_my_applications_carry_order_payment_and_partner() -> None:
    course_session = _course_session()
    program = SimpleNamespace(title="Имплантация")
    rows = [(_enrollment(), course_session, program, None, _order())]

    class _Rows:
        async def list_for_client(self, client_id, exclude_statuses=()):
            return rows

        async def partners(self, client_id, session_ids):
            return {5: COLLEAGUE}

    service = EnrollmentService.__new__(EnrollmentService)
    service.enrollments = _Rows()
    service.orders = _Orders(_order(), paid=2_000_000)
    [app] = asyncio.run(service.my_applications(ME, TODAY))
    assert (app.paid, app.partner, app.self_cancel, app.is_payer) == (2_000_000, COLLEAGUE, True, True)
    assert app.cancel_until == date(2026, 10, 30)


# --- Экраны --------------------------------------------------------------------------------------------------------

def _app(enrollment=None, order=None, paid: int = 0, partner=None, self_cancel: bool = True,
         course_session=None) -> MyApplication:
    course_session = course_session or _course_session()
    return MyApplication(enrollment or _enrollment(), course_session, course_session.program, None, order, paid,
                         partner, self_cancel)


def _rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def test_payer_card_with_unpaid_order() -> None:
    card = render.my_application_card(_app(order=_order(payer_type=PayerType.INDIVIDUAL), partner=COLLEAGUE), TZ, NOW)
    assert "Имплантация &lt;базовый&gt;" in card and "Вместе с Вами:</b> Петрова Анна Сергеевна" in card
    assert "Заказ №100001</b> · " + format_rub(8_000_000) in card
    assert "Оплатить до:</b> 24 октября, 12:00" in card  # 72 часа от 9:00 UTC 21.10 - по Москве
    assert texts.MY_APPLICATION_OVERDUE not in card and "физическое лицо" in card
    assert texts.ENROLLMENT_STATUS_LABELS["application"] in card
    assert "до 30 октября включительно" in card


def test_payer_card_paid_partly_fully_and_overdue() -> None:
    partly = render.my_application_card(_app(enrollment=_enrollment(status=EnrollmentStatus.CONFIRMED),
                                             order=_order(status=OrderStatus.PARTIALLY_PAID), paid=2_000_000), TZ, NOW)
    assert f"Оплачено:</b> {format_rub(2_000_000)}, осталось {format_rub(6_000_000)}" in partly
    assert "Оплатить до" not in partly and "✅ Место закреплено" in partly  # частичная оплата закрепляет место
    full = render.my_application_card(_app(order=_order(status=OrderStatus.PAID), paid=8_000_000), TZ, NOW)
    assert "полностью" in full
    overdue = render.my_application_card(_app(order=_order(due_at=NOW - timedelta(hours=1))), TZ, NOW)
    assert texts.MY_APPLICATION_OVERDUE in overdue
    org = render.my_application_card(_app(order=_order(payer_type=PayerType.ORGANIZATION)), TZ, NOW)
    assert texts.PAYER_ORGANIZATION_WAITING in org
    late = render.my_application_card(_app(order=_order(), self_cancel=False), TZ, NOW)
    assert texts.MY_APPLICATION_CANCEL_LATE in late


def test_colleague_card_hides_the_order() -> None:
    enroller = SimpleNamespace(id=ME, full_name="Иванов Иван Иванович", telegram_id=1)
    app = _app(enrollment=_enrollment(client_id=COLLEAGUE.id, enrolled_by_client_id=ME), order=_order(),
               partner=enroller, self_cancel=False)
    card = render.my_application_card(app, TZ, NOW)
    assert "Вместе с:</b> Иванов Иван Иванович - заказ оплачивает коллега" in card
    assert "Заказ №" not in card and texts.MY_APPLICATION_COLLEAGUE_HINT in card
    assert "Написать менеджеру" not in str(_rows(keyboards.my_application_card(app, 0, 1)))  # без ссылки нет кнопки
    rows = _rows(keyboards.my_application_card(app, 0, 1, "https://t.me/manager"))
    assert rows == [[texts.BTN_CANCEL_VIA_MANAGER], [texts.BTN_BACK_TO_CABINET]]  # ни оплаты, ни отмены


def test_card_buttons_for_payer() -> None:
    app = _app(order=_order())
    markup = keyboards.my_application_card(app, 0, 2)
    assert _rows(markup)[1:] == [[texts.BTN_ORDER_PAY], [texts.BTN_CANCEL_APPLICATION], [texts.BTN_BACK_TO_CABINET]]
    assert markup.inline_keyboard[1][0].callback_data == MyApplicationCb(
        action=MyApplicationAction.PAY, enrollment_id=1, index=0).pack()
    org = keyboards.my_application_card(_app(order=_order(payer_type=PayerType.ORGANIZATION)), 0, 1)
    assert org.inline_keyboard[0][0].text == texts.BTN_ORDER_REQUISITES
    assert org.inline_keyboard[0][0].callback_data == OrderCb(action=OrderAction.ORGANIZATION, order_id=77).pack()
    paid = keyboards.my_application_card(_app(order=_order(status=OrderStatus.PAID), paid=8_000_000), 0, 1)
    assert _rows(paid) == [[texts.BTN_CANCEL_APPLICATION], [texts.BTN_BACK_TO_CABINET]]  # оплачено - платить нечего
    late = keyboards.my_application_card(_app(order=_order(payer_type=PayerType.INDIVIDUAL), self_cancel=False), 0, 1,
                                         "https://t.me/manager")
    assert _rows(late) == [[texts.BTN_CANCEL_VIA_MANAGER], [texts.BTN_BACK_TO_CABINET]]
    waitlist = _app(enrollment=_enrollment(status=EnrollmentStatus.WAITLIST, order_id=None))
    assert _rows(keyboards.my_application_card(waitlist, 0, 1))[0] == [texts.BTN_LEAVE_WAITLIST]
    assert render._cancel_hint(waitlist) is None


def test_cancel_questions() -> None:
    solo = render.cancel_question(_app(order=_order()))
    assert "Заказ №100001 закроется, место освободится" in solo and "добавим Вас в лист" in solo
    pair = render.cancel_question(_app(order=_order(), partner=COLLEAGUE))
    assert "Отменится и запись коллеги: Петрова А. С." in pair and "места освободятся" in pair
    assert "Вас с коллегой" in pair
    waitlist = render.cancel_question(_app(enrollment=_enrollment(status=EnrollmentStatus.WAITLIST, order_id=None),
                                           partner=COLLEAGUE))
    assert "Выйти из листа ожидания?" in waitlist and "Коллега тоже выйдет" in waitlist
    paid = render.cancel_paid_question(_app(order=_order()), 2_000_000)
    assert f"Вы оплатили {format_rub(2_000_000)}" in paid and "Что сделать с оплатой?" in paid


def test_cancel_buttons() -> None:
    markup = keyboards.cancel_paid_choice(1, 2)
    assert _rows(markup) == [[texts.BTN_CANCEL_TRANSFER], [texts.BTN_CANCEL_ADVANCE], [texts.BTN_CANCEL_REFUND],
                             [texts.BTN_CANCEL_KEEP]]
    assert [row[0].callback_data for row in markup.inline_keyboard] == [
        MyApplicationCb(action=action, enrollment_id=1, index=2).pack()
        for action in (MyApplicationAction.TRANSFER, MyApplicationAction.ADVANCE, MyApplicationAction.REFUND,
                       MyApplicationAction.KEEP)]
    assert _rows(keyboards.cancelled_actions(0, schedule=True)) == [[texts.BTN_TO_SCHEDULE],
                                                                    [texts.BTN_TO_MY_APPLICATIONS]]
    assert _rows(keyboards.cancelled_actions(0, manager_url="https://t.me/manager")) == [
        [texts.BTN_CANCEL_VIA_MANAGER], [texts.BTN_TO_MY_APPLICATIONS]]
    assert _rows(keyboards.payer_choice(77, back_index=1))[-1] == [texts.BTN_TO_MY_APPLICATIONS]


def test_cancelled_answers() -> None:
    def result(**kw) -> CancelResult:
        return CancelResult(CancelOutcome.CANCELLED, order=_order(), **kw)

    paid = format_rub(2_000_000)
    assert paid in render.cancelled(result(paid=2_000_000, choice=CancelChoice.TRANSFER), True)
    assert "аванс" in render.cancelled(result(paid=2_000_000, choice=CancelChoice.ADVANCE), True)
    assert "напишите менеджеру" in render.cancelled(result(paid=2_000_000, choice=CancelChoice.REFUND), True)
    assert "свяжется" in render.cancelled(result(paid=2_000_000, choice=CancelChoice.REFUND), False)
    assert "заказ №100001 закрыт" in render.cancelled(result(course_waitlist=True), True)
    assert texts.CANCELLED_WHO_PAIR in render.cancelled(result(course_waitlist=True, colleagues=(COLLEAGUE,)), True)
    assert render.cancelled(result(waitlist=True), True) == texts.CANCELLED_WAITLIST
    note = render.colleague_cancelled(_app(), "Иванов <И. И.>")
    assert "Иванов &lt;И. И.&gt;" in note and "Имплантация &lt;базовый&gt;" in note


def test_manager_drafts_and_popups() -> None:
    assert render.manager_cancel_draft(_app(order=_order())).endswith("заказ №100001.")
    colleague = _app(enrollment=_enrollment(enrolled_by_client_id=ME), order=_order())
    assert "заказ" not in render.manager_cancel_draft(colleague)  # номер заказа знает плательщик
    assert render.manager_refund_draft(_order()) == "Здравствуйте! Прошу вернуть оплату по заказу №100001."
    assert len(texts.APPLICATION_CANNOT_CANCEL) <= 200 and "<" not in texts.APPLICATION_CANNOT_CANCEL


def test_cabinet_advance_line_is_optional() -> None:
    fields = dict(name="И", birth_date="—", city="—", phone="—", email="—", workplace="—", specialties="—",
                  positions="—", experience="—", marketing="включены", referral_link="x", referrals=0)
    without = texts.CABINET.format(advance="", **fields)
    assert "Аванс" not in without and "включены\n\n🎁" in without  # без аванса - как раньше
    with_advance = texts.CABINET.format(advance=texts.CABINET_ADVANCE.format(amount=format_rub(2_000_000)), **fields)
    assert f"💰 <b>Аванс:</b> {format_rub(2_000_000)}" in with_advance and "курса\n\n🎁" in with_advance
