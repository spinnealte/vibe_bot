"""Заказы в админке (полная версия, этап 1, шаг 4; решения 21.10.2026): разделы, карточка, оплата, аванс, бронь.

Деньги отмечает только админ. Оплата - строка payments (по счёту, наличными или картой / СБП; из аванса - способ
advance); любая оплата закрепляет место: записи заказа становятся «подтверждены». Сумма больше остатка не
принимается - переплата идёт через аванс. Повтор той же отметки (двойное нажатие) отсекает уникальный ключ
платежа.

Блокировки - в том же порядке, что у отмены клиентом (services/enrollments.py): строка заказа, потом записи.
Аванс клиента меняется под блокировкой строки клиента: два списания сразу не уведут баланс в минус.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import (
    AdvanceReason,
    EnrollmentStatus,
    OrderStatus,
    PaymentMethod,
    PaymentProvider,
    PaymentStatus,
    PaymentState,
)
from stubbot.db.models import Advance, Client, CourseSession, Enrollment, Order, Payment, PriceOption
from stubbot.repositories.enrollments import EnrollmentRepository
from stubbot.repositories.orders import AdvanceRepository, OrderRepository
from stubbot.services.admin_log import log_admin_action
from stubbot.services.enrollments import PAYABLE_ORDER_STATUSES

logger = logging.getLogger(__name__)

ADMIN_REMOVE_REASON = "Бронь снята менеджером"
LOG_SECTION = "заказ"


class OrderCategory(StrEnum):
    """Разделы «🧾 Заказы»."""

    AWAITING = "wait"  # ждут оплаты (вместе с просроченными)
    OVERDUE = "late"  # ждут оплаты, срок вышел
    PARTIAL = "part"  # оплачены частично - место закреплено, остаток на контроле
    PAID = "paid"  # оплачены полностью - только курсы, которые ещё не прошли
    MONEY = "money"  # отменены, а деньги по ним ещё не распределены (аванс или возврат)


@dataclass(frozen=True)
class AdminOrderCard:
    """Всё о заказе для карточки в админке."""

    order: Order
    session: CourseSession
    price: PriceOption | None
    payer: Client
    participants: list[tuple[Enrollment, Client]]
    paid: int  # оплачено, копейки
    advance: int  # аванс плательщика сейчас
    money_settled: bool = False  # отменённый заказ: деньги уже распределены
    online_pending: Payment | None = None  # клиенту выдана ссылка на онлайн-оплату, итога ещё нет

    @property
    def left(self) -> int | None:
        """Осталось оплатить. None - сумма заказа не указана."""
        total = self.order.total_amount
        return None if total is None else max(total - self.paid, 0)

    @property
    def payable(self) -> bool:
        return self.order.status in PAYABLE_ORDER_STATUSES

    @property
    def money_pending(self) -> bool:
        return self.order.status is OrderStatus.CANCELLED and self.paid > 0 and not self.money_settled

    def overdue(self, now: datetime) -> bool:
        return self.order.status is OrderStatus.AWAITING_PAYMENT and self.order.due_at < now

    @property
    def advance_to_apply(self) -> int:
        """Сколько можно зачесть из аванса: не больше баланса и остатка. 0 - нечего."""
        if not self.payable or self.left is None:
            return 0
        return min(self.advance, self.left)

    def category(self, now: datetime) -> OrderCategory | None:
        """Раздел, где заказ сейчас лежит, - туда ведёт «⬅️ К заказам». None - ни в одном (всё закрыто)."""
        status = self.order.status
        if status is OrderStatus.AWAITING_PAYMENT:
            return OrderCategory.OVERDUE if self.overdue(now) else OrderCategory.AWAITING
        if status is OrderStatus.PARTIALLY_PAID:
            return OrderCategory.PARTIAL
        if status is OrderStatus.PAID:
            return OrderCategory.PAID
        return OrderCategory.MONEY if self.money_pending else None


class Outcome(StrEnum):
    DONE = "done"
    DUPLICATE = "duplicate"  # та же отметка оплаты уже сохранена (двойное нажатие)
    NOT_ALLOWED = "not_allowed"  # заказ не в том состоянии (уже оплачен, отменён, деньги распределены)
    TOO_MUCH = "too_much"  # больше остатка
    NO_ADVANCE = "no_advance"  # зачесть из аванса нечего


@dataclass(frozen=True)
class ActionResult:
    outcome: Outcome
    order: Order | None = None
    amount: int = 0  # сумма действия: оплата, зачисление, возврат
    paid: int = 0  # оплачено по заказу после действия
    full: bool = False  # заказ оплачен полностью
    balance: int = 0  # аванс клиента после действия
    participants: list[tuple[Enrollment, Client]] = field(default_factory=list)
    course_waitlist: bool = False  # снятие брони без оплаты: участники в листе ожидания курса


class AdminOrderService:
    def __init__(self, session: AsyncSession, admin_telegram_id: int | None) -> None:
        self.db = session
        self.orders = OrderRepository(session)
        self.advances = AdvanceRepository(session)
        self.enrollments = EnrollmentRepository(session)
        self.admin_id = admin_telegram_id  # кто действует - в платёж, аванс и журнал logs/admin.log

    # --- Разделы и карточка ------------------------------------------------------------------------------------

    async def category(self, category: OrderCategory, today: date, now: datetime) -> list[tuple[Order, Client]]:
        match category:
            case OrderCategory.AWAITING:
                return await self.orders.with_payers((OrderStatus.AWAITING_PAYMENT,))
            case OrderCategory.OVERDUE:
                return await self.orders.with_payers((OrderStatus.AWAITING_PAYMENT,), due_before=now)
            case OrderCategory.PARTIAL:
                return await self.orders.with_payers((OrderStatus.PARTIALLY_PAID,))
            case OrderCategory.PAID:
                return await self.orders.with_payers((OrderStatus.PAID,), not_past=today)
            case OrderCategory.MONEY:
                return await self.orders.money_pending()

    async def counts(self, today: date, now: datetime) -> dict[OrderCategory, int]:
        return {category: len(await self.category(category, today, now)) for category in OrderCategory}

    async def card(self, order_id: int) -> AdminOrderCard | None:
        order = await self.orders.get(order_id)
        if order is None:
            return None
        paid = (await self.orders.paid_amounts([order.id])).get(order.id, 0)
        return AdminOrderCard(
            order=order,
            session=await self.orders.session_for_card(order.session_id),
            price=await self.orders.price(order.price_option_id),
            payer=await self.orders.client(order.payer_client_id),
            participants=await self.orders.participants(order.id),
            paid=paid,
            advance=await self.advances.balance(order.payer_client_id),
            money_settled=order.status is OrderStatus.CANCELLED and await self.orders.money_settled(order.id),
            online_pending=await self.orders.pending_online(order.id),
        )

    async def find(self, number: int) -> Order | None:
        return await self.orders.get_by_number(number)

    # --- Оплата ------------------------------------------------------------------------------------------------

    async def mark_payment(self, order_id: int, amount: int, method: PaymentMethod, token: str,
                           now: datetime | None = None) -> ActionResult:
        """Отметить поступившую сумму. token - одноразовый ключ черновика: повтор той же отметки не пройдёт."""
        now = now or datetime.now(UTC)
        order = await self.orders.lock(order_id)
        if order is None or order.status not in PAYABLE_ORDER_STATUSES:
            return ActionResult(Outcome.NOT_ALLOWED, order)
        paid = await self._paid(order)
        if amount <= 0 or (order.total_amount is not None and paid + amount > order.total_amount):
            return ActionResult(Outcome.TOO_MUCH, order, paid=paid)
        try:
            async with self.db.begin_nested():
                self.orders.add_payment(self._payment(order, amount, method, now, idempotency_key=f"admin:{token}"))
                await self.db.flush()
        except IntegrityError:
            return ActionResult(Outcome.DUPLICATE, order, paid=paid)
        result = await self.after_payment(order, amount, paid + amount, now)
        log_admin_action(self.admin_id, LOG_SECTION, "№%s: оплата %s коп. (%s)", order.number, amount, method.value)
        return result

    async def set_total(self, order_id: int, amount: int) -> Order | None:
        """Сумма заказа, которую при записи не знали («стоимость уточнит менеджер»). Указанную не меняем."""
        order = await self.orders.lock(order_id)
        if order is None or order.status not in PAYABLE_ORDER_STATUSES or order.total_amount is not None:
            return None
        if amount < await self._paid(order):
            return None  # оплачено уже больше - такой суммы заказа быть не может
        order.total_amount = amount
        log_admin_action(self.admin_id, LOG_SECTION, "№%s: сумма заказа %s коп.", order.number, amount)
        return order

    async def mark_invoice_sent(self, order_id: int, now: datetime | None = None) -> None:
        order = await self.orders.get(order_id)
        if order is not None:
            order.invoice_sent_at = now or datetime.now(UTC)
            log_admin_action(self.admin_id, LOG_SECTION, "№%s: счёт отправлен клиенту", order.number)

    # --- Снятие брони ------------------------------------------------------------------------------------------

    async def remove_booking(self, order_id: int, now: datetime | None = None) -> ActionResult:
        """Снять бронь: места освобождаются, заказ отменён. Без оплаты - участники в листе ожидания курса
        (решение 21.10.2026); была оплата - заказ ждёт в «Деньги после отмены»: аванс или возврат."""
        now = now or datetime.now(UTC)
        order = await self.orders.lock(order_id)
        if order is None or order.status is OrderStatus.CANCELLED:
            return ActionResult(Outcome.NOT_ALLOWED, order)
        participants = await self.orders.participants(order.id, lock=True)
        active = [(row, person) for row, person in participants if row.status not in _CLOSED]
        for row, _ in active:
            row.status = EnrollmentStatus.CANCELLED_BY_ADMIN
            row.cancelled_at = now
            row.cancel_reason = ADMIN_REMOVE_REASON
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = now
        order.cancel_reason = ADMIN_REMOVE_REASON
        paid = await self._paid(order)
        if not paid:
            course_session = await self.enrollments.get_session(order.session_id)
            for _, person in active:
                await self.enrollments.put_on_course_waitlist(person.id, course_session.program_id)
        log_admin_action(self.admin_id, LOG_SECTION, "№%s: бронь снята, оплачено %s коп.", order.number, paid)
        return ActionResult(Outcome.DONE, order, paid=paid, participants=active, course_waitlist=not paid)

    # --- Аванс -------------------------------------------------------------------------------------------------

    async def credit_advance(self, order_id: int) -> ActionResult:
        """Оплаченное по отменённому заказу - на аванс плательщика."""
        order, paid = await self._money_to_settle(order_id)
        if order is None:
            return ActionResult(Outcome.NOT_ALLOWED)
        self.advances.add(self._advance(order, paid, AdvanceReason.CANCELLED_ORDER))
        await self.db.flush()
        balance = await self.advances.balance(order.payer_client_id)
        log_admin_action(self.admin_id, LOG_SECTION, "№%s: на аванс %s коп.", order.number, paid)
        return ActionResult(Outcome.DONE, order, amount=paid, paid=paid, balance=balance)

    async def mark_refunded(self, order_id: int) -> ActionResult:
        """Деньги по отменённому заказу вернули: в журнале - зачисление и возврат, баланс не меняется."""
        order, paid = await self._money_to_settle(order_id)
        if order is None:
            return ActionResult(Outcome.NOT_ALLOWED)
        self.advances.add(self._advance(order, paid, AdvanceReason.CANCELLED_ORDER))
        self.advances.add(self._advance(order, -paid, AdvanceReason.REFUND))
        await self.db.flush()
        log_admin_action(self.admin_id, LOG_SECTION, "№%s: возврат %s коп.", order.number, paid)
        return ActionResult(Outcome.DONE, order, amount=paid, paid=paid,
                            balance=await self.advances.balance(order.payer_client_id))

    async def apply_advance(self, order_id: int, now: datetime | None = None) -> ActionResult:
        """Зачесть аванс плательщика в заказ: сколько есть, но не больше остатка."""
        now = now or datetime.now(UTC)
        order = await self.orders.lock(order_id)
        if order is None or order.status not in PAYABLE_ORDER_STATUSES or order.total_amount is None:
            return ActionResult(Outcome.NOT_ALLOWED, order)
        await self.advances.lock_client(order.payer_client_id)
        paid = await self._paid(order)
        amount = min(await self.advances.balance(order.payer_client_id), order.total_amount - paid)
        if amount <= 0:
            return ActionResult(Outcome.NO_ADVANCE, order, paid=paid)
        self.orders.add_payment(self._payment(order, amount, PaymentMethod.ADVANCE, now))
        self.advances.add(self._advance(order, -amount, AdvanceReason.APPLIED_TO_ORDER))
        await self.db.flush()
        result = await self.after_payment(order, amount, paid + amount, now)
        log_admin_action(self.admin_id, LOG_SECTION, "№%s: зачтено из аванса %s коп.", order.number, amount)
        return result

    # --- Вспомогательное ---------------------------------------------------------------------------------------

    async def _paid(self, order: Order) -> int:
        return (await self.orders.paid_amounts([order.id])).get(order.id, 0)

    async def _money_to_settle(self, order_id: int) -> tuple[Order | None, int]:
        """Отменённый заказ с нераспределённой оплатой (под блокировкой заказа и клиента) и сколько оплачено."""
        order = await self.orders.lock(order_id)
        if order is None or order.status is not OrderStatus.CANCELLED or await self.orders.money_settled(order.id):
            return None, 0
        paid = await self._paid(order)
        if not paid:
            return None, 0
        await self.advances.lock_client(order.payer_client_id)
        return order, paid

    async def after_payment(self, order: Order, amount: int, paid: int, now: datetime) -> ActionResult:
        """После оплаты: статус заказа и записи участников - любая оплата закрепляет место. Заказ уже под
        блокировкой. Тот же шаг и у онлайн-оплаты (services/online_payments.py)."""
        full = order.total_amount is not None and paid >= order.total_amount
        order.status = OrderStatus.PAID if full else OrderStatus.PARTIALLY_PAID
        if full:
            order.paid_at = now
        participants = await self.orders.participants(order.id, lock=True)
        for row, _ in participants:
            if row.status is EnrollmentStatus.APPLICATION:
                row.status = EnrollmentStatus.CONFIRMED
                row.confirmed_at = now
            if row.status is EnrollmentStatus.CONFIRMED:
                row.payment_status = PaymentStatus.PAID if full else PaymentStatus.PARTIAL
        balance = await self.advances.balance(order.payer_client_id)
        return ActionResult(Outcome.DONE, order, amount=amount, paid=paid, full=full, balance=balance,
                            participants=participants)

    def _payment(self, order: Order, amount: int, method: PaymentMethod, now: datetime,
                 idempotency_key: str | None = None) -> Payment:
        return Payment(order_id=order.id, amount=amount, currency=order.currency, method=method,
                       provider=PaymentProvider.MANUAL, state=PaymentState.SUCCEEDED, idempotency_key=idempotency_key,
                       paid_at=now, created_by_telegram_id=self.admin_id)

    def _advance(self, order: Order, amount: int, reason: AdvanceReason) -> Advance:
        return Advance(client_id=order.payer_client_id, order_id=order.id, amount=amount, currency=order.currency,
                       reason=reason, created_by_telegram_id=self.admin_id or 0)


# Записи, которые снятие брони не трогает: уже отменены или курс прошёл.
_CLOSED = (EnrollmentStatus.CANCELLED_BY_CLIENT, EnrollmentStatus.CANCELLED_BY_ADMIN, EnrollmentStatus.ATTENDED,
           EnrollmentStatus.NO_SHOW, EnrollmentStatus.COMPLETED)
