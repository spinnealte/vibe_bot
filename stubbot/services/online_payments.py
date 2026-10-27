"""Онлайн-оплата заказа клиентом (полная версия, этап 1, шаг 7: каркас под ЮKassa, выключен до договора).

- «💳 Оплатить онлайн» - только плательщик, только действующий заказ с известной суммой, всегда весь остаток
  (Чеки от ЮKassa не принимают частичную предоплату; частями - по счёту, как раньше).
- prepare: под блокировкой заказа - строка payments «pending» со своим ключом от повторного создания. Ссылку,
  выданную меньше LINK_TTL назад на ту же сумму, выдаём снова - второй платёж не создаём.
- Сам платёж у провайдера создаёт tg/online_payments.py уже после коммита этой строки: запрос к ЮKassa не держит
  блокировку заказа, а ключ лежит в базе ещё до запроса.
- apply: итог от провайдера - под блокировкой заказа, потом платежа (тот же порядок, что у отмены и отметки админа).
  Успех - платёж succeeded, дальше как оплата от админа (AdminOrderService.after_payment): место закреплено, статус
  заказа. Отказ - canceled с причиной провайдера. Применяется один раз, сколько бы раз ни проверяли.
- Деньги пришли, а заказ уже отменён или оплачен другим способом - платёж всё равно записан (деньги получены), админам
  предупреждение. Отменённый заказ с оплатой сам попадает в «Деньги после отмены» - там аванс или возврат.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import OrderStatus, PaymentMethod, PaymentProvider, PaymentState
from stubbot.db.models import Client, CourseSession, Order, Payment
from stubbot.payments.gateway import GatewayPayment, GatewayStatus
from stubbot.repositories.orders import OrderRepository
from stubbot.services.admin_orders import ActionResult, AdminOrderService
from stubbot.services.enrollments import PAYABLE_ORDER_STATUSES

logger = logging.getLogger(__name__)

LINK_TTL = timedelta(minutes=50)  # у ЮKassa на оплату 1 час: ссылку старше не выдаём - истечёт на странице оплаты
CREATE_GRACE = timedelta(minutes=10)  # строка без номера у провайдера дольше этого - платёж так и не создался
# Как часто спрашивать статус: пока идёт час на оплату - раз в минуту, потом реже (итог к тому времени почти всегда
# есть: ЮKassa сама отменяет неоплаченный платёж через час).
CHECK_SCHEDULE = ((timedelta(minutes=70), timedelta(minutes=1)), (timedelta(days=1), timedelta(minutes=15)))
CHECK_LATE = timedelta(hours=6)
NOT_CREATED = "not_created"  # причина отмены: провайдер не создал платёж (ссылку клиент не получил)
KEY_PREFIX = "online:"


class StartOutcome(StrEnum):
    READY = "ready"  # ссылка есть (новая или выданная недавно)
    NOT_PAYABLE = "not_payable"  # чужой, отменённый или уже оплаченный заказ
    NO_AMOUNT = "no_amount"  # сумму заказа ещё уточняет менеджер
    NOTHING_LEFT = "nothing_left"  # оплачено всё
    NEED_EMAIL = "need_email"  # чек 54-ФЗ уходит на e-mail, а его нет в профиле
    ERROR = "error"  # провайдер не создал платёж (ставит tg/online_payments.py)


class Problem(StrEnum):
    """Деньги пришли, но не туда, куда ждали, - админам предупреждение."""

    OVERPAID = "overpaid"  # заказ уже был оплачен (например, по счёту) - пришло больше нужного
    ORDER_CANCELLED = "cancelled"  # заказ отменили, пока клиент платил по ссылке
    AMOUNT_CHANGED = "amount"  # провайдер сообщил другую сумму, чем выставлял бот


@dataclass(frozen=True)
class Prepared:
    outcome: StartOutcome
    order: Order | None = None
    payment: Payment | None = None
    reused: bool = False  # ссылка выдана раньше - создавать платёж не нужно
    title: str = ""  # название курса - в описание платежа и чек


@dataclass(frozen=True)
class Applied:
    """Платёж и заказ после проверки итога. changed - итог применён сейчас (уведомить); иначе он уже был или его
    ещё нет. course_session и payer - для писем и экрана «Оплата получена»."""

    payment: Payment
    order: Order
    course_session: CourseSession
    payer: Client
    changed: bool = False
    paid: int = 0  # оплачено по заказу всего
    extra: int = 0  # переплата
    problem: Problem | None = None
    method_type: str | None = None  # чем заплатили (от провайдера) - в уведомление админам
    test: bool = False  # тестовый магазин или тестовая оплата
    result: ActionResult | None = None

    @property
    def full(self) -> bool:
        total = self.order.total_amount
        return total is not None and self.paid >= total


def check_interval(age: timedelta) -> timedelta:
    """Через сколько снова спросить статус платежа такого возраста."""
    return next((every for until, every in CHECK_SCHEDULE if age < until), CHECK_LATE)


def course_title(course_session: CourseSession) -> str:
    return course_session.title_override or course_session.program.title


class OnlinePaymentService:
    def __init__(self, session: AsyncSession, provider: PaymentProvider) -> None:
        self.db = session
        self.provider = provider
        self.orders = OrderRepository(session)
        self.admin_orders = AdminOrderService(session, None)  # переход заказа после оплаты - общий с админкой

    async def prepare(self, client_id: int, email: str | None, order_id: int, now: datetime,
                      need_email: bool = False) -> Prepared:
        """Строка платежа на весь остаток (или выданная недавно ссылка). order_id из кнопки может быть подделан -
        заказ ищем только среди своих."""
        order = await self.orders.lock(order_id)
        if order is None or order.payer_client_id != client_id or order.status not in PAYABLE_ORDER_STATUSES:
            return Prepared(StartOutcome.NOT_PAYABLE)
        if order.total_amount is None:
            return Prepared(StartOutcome.NO_AMOUNT, order)
        left = order.total_amount - await self._paid(order)
        if left <= 0:
            return Prepared(StartOutcome.NOTHING_LEFT, order)
        if need_email and not email:
            return Prepared(StartOutcome.NEED_EMAIL, order)
        title = course_title(await self.orders.session_for_card(order.session_id))
        pending = await self.orders.pending_online(order.id)
        if (pending is not None and pending.provider is self.provider and pending.provider_payment_id
                and pending.amount == left and now - pending.created_at < LINK_TTL):
            return Prepared(StartOutcome.READY, order, pending, reused=True, title=title)
        payment = Payment(order_id=order.id, amount=left, currency=order.currency, method=PaymentMethod.CARD_ONLINE,
                          provider=self.provider, state=PaymentState.PENDING,
                          idempotency_key=KEY_PREFIX + secrets.token_hex(16))
        self.orders.add_payment(payment)
        await self.db.flush()
        logger.info("Заказ №%s: онлайн-оплата, платёж #%s", order.number, payment.id)
        return Prepared(StartOutcome.READY, order, payment, title=title)

    async def attach(self, payment_id: int, remote: GatewayPayment, now: datetime) -> Payment:
        """Провайдер создал платёж: его номер и ссылку - в нашу строку."""
        payment = await self.orders.lock_payment(payment_id)
        payment.provider_payment_id = remote.id
        payment.confirmation_url = remote.confirmation_url
        payment.checked_at = now
        return payment

    async def give_up(self, payment_id: int, reason: str, now: datetime) -> None:
        """Платёж не создан - строку закрываем: ссылки у клиента нет, заплатить по ней нельзя."""
        payment = await self.orders.lock_payment(payment_id)
        if payment is not None and payment.state is PaymentState.PENDING:
            payment.state = PaymentState.CANCELED
            payment.cancel_reason = reason
            payment.checked_at = now

    async def touch(self, payment_id: int, now: datetime) -> None:
        """Статус спросить не удалось - следующая попытка по расписанию, а не на каждой проверке."""
        payment = await self.orders.get_payment(payment_id)
        if payment is not None:
            payment.checked_at = now

    async def find(self, payment_id: int, client_id: int | None = None) -> Payment | None:
        """Онлайн-платёж этого провайдера. client_id - только по заказу, который оплачивает этот клиент (id из кнопки
        может быть подделан); None - любой (фоновая сверка)."""
        payment = await self.orders.get_payment(payment_id)
        if payment is None or payment.provider is not self.provider:
            return None
        if client_id is None:
            return payment
        order = await self.orders.get(payment.order_id)
        return payment if order is not None and order.payer_client_id == client_id else None

    async def current(self, payment: Payment) -> Applied:
        """Как сейчас: без запроса к провайдеру (итог уже есть или платёж ещё создаётся)."""
        return await self._as_is(payment, await self.orders.get(payment.order_id))

    async def apply(self, payment_id: int, remote: GatewayPayment, now: datetime) -> Applied | None:
        """Итог от провайдера - в базу. None - такого платежа нет."""
        found = await self.orders.get_payment(payment_id)
        if found is None:
            return None
        order = await self.orders.lock(found.order_id)
        payment = await self.orders.lock_payment(payment_id)
        if payment.state is not PaymentState.PENDING:
            return await self._as_is(payment, order)  # итог уже применён - кнопкой или фоновой сверкой
        payment.checked_at = now
        if remote.status is GatewayStatus.CANCELED:
            payment.state = PaymentState.CANCELED
            payment.cancel_reason = (remote.cancel_reason or "")[:64] or None
            logger.info("Заказ №%s: онлайн-платёж #%s не прошёл (%s)", order.number, payment.id, payment.cancel_reason)
            return await self._applied(payment, order, changed=True, test=remote.test)
        if remote.status is not GatewayStatus.SUCCEEDED:
            return await self._applied(payment, order)
        paid_before = await self._paid(order)
        problem = None
        if remote.amount != payment.amount:
            logger.warning("Заказ №%s: провайдер сообщил другую сумму онлайн-платежа #%s", order.number, payment.id)
            payment.amount = remote.amount  # записываем, сколько пришло на самом деле
            problem = Problem.AMOUNT_CHANGED
        payment.state = PaymentState.SUCCEEDED
        payment.paid_at = now
        payment.method = PaymentMethod.SBP if remote.method_type == "sbp" else PaymentMethod.CARD_ONLINE
        await self.db.flush()
        paid = paid_before + payment.amount
        extra = max(paid - order.total_amount, 0) if order.total_amount is not None else 0
        result = None
        if order.status in PAYABLE_ORDER_STATUSES:
            result = await self.admin_orders.after_payment(order, payment.amount, paid, now)
        elif order.status is OrderStatus.CANCELLED:
            problem, extra = Problem.ORDER_CANCELLED, 0
        if extra and problem is None:
            problem = Problem.OVERPAID
        logger.info("Заказ №%s: онлайн-платёж #%s прошёл%s", order.number, payment.id,
                    f" ({problem.value})" if problem else "")
        return await self._applied(payment, order, changed=True, paid=paid, extra=extra, problem=problem,
                                   method_type=remote.method_type, test=remote.test, result=result)

    async def due_for_check(self, now: datetime, limit: int) -> tuple[list[tuple[int, str]], int]:
        """Фоновая сверка: (наш id, номер у провайдера) платежей, чей статус пора спросить, и сколько строк закрыто
        как «не создан» (запрос к провайдеру оборвался, ссылку клиент не получил)."""
        due, closed = [], 0
        for payment in await self.orders.pending_payments(self.provider):
            age = now - payment.created_at
            if payment.provider_payment_id is None:
                if age > CREATE_GRACE:
                    payment.state = PaymentState.CANCELED
                    payment.cancel_reason = NOT_CREATED
                    closed += 1
                continue
            if payment.checked_at is None or now - payment.checked_at >= check_interval(age):
                due.append((payment.id, payment.provider_payment_id))
        return due[:limit], closed

    async def _paid(self, order: Order) -> int:
        return (await self.orders.paid_amounts([order.id])).get(order.id, 0)

    async def _as_is(self, payment: Payment, order: Order) -> Applied:
        """Итог, применённый раньше: экран «Оплата получена» должен знать про отмену заказа и переплату."""
        paid = await self._paid(order)
        problem, extra = None, 0
        if payment.state is PaymentState.SUCCEEDED:
            if order.status is OrderStatus.CANCELLED:
                problem = Problem.ORDER_CANCELLED
            elif order.total_amount is not None and paid > order.total_amount:
                problem, extra = Problem.OVERPAID, paid - order.total_amount
        return await self._applied(payment, order, paid=paid, problem=problem, extra=extra)

    async def _applied(self, payment: Payment, order: Order, **fields) -> Applied:
        if "paid" not in fields:
            fields["paid"] = await self._paid(order)
        return Applied(payment=payment, order=order, course_session=await self.orders.session_for_card(order.session_id),
                       payer=await self.orders.client(order.payer_client_id), **fields)
