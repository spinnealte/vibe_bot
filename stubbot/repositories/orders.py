from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.enums import AdvanceReason, OrderStatus, PaymentProvider, PaymentState
from stubbot.db.models import Advance, Client, CourseSession, Enrollment, Order, Payment, PriceOption


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, order: Order) -> Order:
        self.session.add(order)
        await self.session.flush()  # номер заказа выдаёт база - он нужен сразу
        return order

    async def get(self, order_id: int) -> Order | None:
        return await self.session.get(Order, order_id)

    async def get_by_number(self, number: int) -> Order | None:
        return await self.session.scalar(select(Order).where(Order.number == number))

    async def get_for_payer(self, order_id: int, client_id: int) -> Order | None:
        """Только свой заказ: id из кнопки может быть подделан."""
        return await self.session.scalar(select(Order).where(Order.id == order_id, Order.payer_client_id == client_id))

    async def lock(self, order_id: int) -> Order | None:
        """Заказ с блокировкой строки до конца транзакции: отмена клиентом и отметка оплаты админом не пересекутся
        (иначе отмена «без оплаты» может проскочить мимо только что внесённых денег). Строка перечитывается."""
        stmt = select(Order).where(Order.id == order_id).with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(stmt)

    async def paid_amounts(self, order_ids: list[int]) -> dict[int, int]:
        """Оплачено по заказам, в копейках: сумма успешных платежей. Заказа без платежей в ответе нет."""
        if not order_ids:
            return {}
        stmt = (
            select(Payment.order_id, func.sum(Payment.amount))
            .where(Payment.order_id.in_(order_ids), Payment.state == PaymentState.SUCCEEDED)
            .group_by(Payment.order_id)
        )
        return {order_id: int(total) for order_id, total in (await self.session.execute(stmt)).all()}

    def add_payment(self, payment: Payment) -> None:
        self.session.add(payment)

    # --- Онлайн-оплата (шаг 7) -----------------------------------------------------------------------------------

    async def get_payment(self, payment_id: int) -> Payment | None:
        return await self.session.get(Payment, payment_id)

    async def lock_payment(self, payment_id: int) -> Payment | None:
        """Платёж с блокировкой строки - после строки заказа: итог одного платежа применяется один раз, даже если
        его одновременно проверяют кнопкой и фоновой сверкой."""
        stmt = (select(Payment).where(Payment.id == payment_id).with_for_update()
                .execution_options(populate_existing=True))
        return await self.session.scalar(stmt)

    async def pending_online(self, order_id: int) -> Payment | None:
        """Последняя выданная, но ещё не оплаченная ссылка на онлайн-оплату заказа."""
        stmt = (
            select(Payment)
            .where(Payment.order_id == order_id, Payment.state == PaymentState.PENDING,
                   Payment.provider != PaymentProvider.MANUAL)
            .order_by(Payment.id.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def pending_payments(self, provider: PaymentProvider) -> list[Payment]:
        """Все онлайн-платежи провайдера, которые ждут итога, - для фоновой сверки (их единицы)."""
        stmt = (select(Payment).where(Payment.state == PaymentState.PENDING, Payment.provider == provider)
                .order_by(Payment.id))
        return list(await self.session.scalars(stmt))

    # --- Админка ------------------------------------------------------------------------------------------------

    async def with_payers(self, statuses: tuple[OrderStatus, ...], due_before: datetime | None = None,
                          not_past: date | None = None,
                          invoice_sent: bool | None = None) -> list[tuple[Order, Client]]:
        """Заказы со статусами и их плательщики; старые первыми.

        due_before - только просроченные (срок оплаты раньше); not_past - только курсы, которые ещё не прошли
        (начало не раньше этого дня или дат нет); invoice_sent - только со счётом (True) или без счёта (False).
        """
        stmt = (
            select(Order, Client)
            .join(Client, Client.id == Order.payer_client_id)
            .where(Order.status.in_(statuses))
            .order_by(Order.due_at, Order.id)
        )
        if due_before is not None:
            stmt = stmt.where(Order.due_at < due_before)
        if invoice_sent is not None:
            stmt = stmt.where(Order.invoice_sent_at.is_not(None) if invoice_sent else Order.invoice_sent_at.is_(None))
        if not_past is not None:
            stmt = stmt.join(CourseSession, CourseSession.id == Order.session_id).where(
                or_(CourseSession.start_date.is_(None), CourseSession.start_date >= not_past)
            )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def money_pending(self) -> list[tuple[Order, Client]]:
        """Отменённые заказы, по которым оплачено больше, чем распределено (на аванс или возврат). По суммам: деньги
        бывают и после распределения - онлайн-оплата по старой ссылке."""
        paid = (select(func.coalesce(func.sum(Payment.amount), 0))
                .where(Payment.order_id == Order.id, Payment.state == PaymentState.SUCCEEDED).scalar_subquery())
        stmt = (
            select(Order, Client)
            .join(Client, Client.id == Order.payer_client_id)
            .where(Order.status == OrderStatus.CANCELLED, paid > self._settled_sum(Order.id))
            .order_by(Order.cancelled_at, Order.id)
        )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    # --- Напоминания об оплате (фоновая задача) ------------------------------------------------------------------

    async def lock_overdue(self, now: datetime, today: date, limit: int) -> list[Order]:
        """Неоплаченные заказы с истёкшим сроком, о которых ещё не сообщали, - курс ещё не начался."""
        return await self._lock_unpaid(today, limit, Order.overdue_notified_at.is_(None), Order.due_at <= now)

    async def lock_unreminded(self, created_before: datetime, now: datetime, today: date, limit: int) -> list[Order]:
        """Неоплаченные заказы старше суток без напоминания, срок оплаты ещё не вышел, курс не начался."""
        return await self._lock_unpaid(today, limit, Order.payment_reminded_at.is_(None),
                                       Order.created_at <= created_before, Order.due_at > now)

    async def _lock_unpaid(self, today: date, limit: int, *conditions) -> list[Order]:
        # SKIP LOCKED: заказ, который сейчас меняет админ или клиент, возьмём при следующей проверке.
        stmt = (
            select(Order)
            .join(CourseSession, CourseSession.id == Order.session_id)
            .where(Order.status == OrderStatus.AWAITING_PAYMENT,
                   or_(CourseSession.start_date.is_(None), CourseSession.start_date > today), *conditions)
            .order_by(Order.id)
            .limit(limit)
            .with_for_update(of=Order, skip_locked=True)
        )
        return list(await self.session.scalars(stmt))

    async def settled_amount(self, order_id: int) -> int:
        """Сколько из оплаченного по отменённому заказу уже распределено: зачисления на аванс по нему в журнале
        (возврат - тоже зачисление, следом списание)."""
        return int(await self.session.scalar(select(self._settled_sum(order_id))))

    @staticmethod
    def _settled_sum(order):
        return (select(func.coalesce(func.sum(Advance.amount), 0))
                .where(Advance.order_id == order, Advance.reason == AdvanceReason.CANCELLED_ORDER).scalar_subquery())

    async def session_for_card(self, session_id: int) -> CourseSession:
        """Проведение с курсом и днями - для карточки заказа и сообщений о нём."""
        stmt = (
            select(CourseSession)
            .where(CourseSession.id == session_id)
            .options(selectinload(CourseSession.program), selectinload(CourseSession.days))
        )
        return (await self.session.scalars(stmt)).one()

    async def price(self, price_option_id: int | None) -> PriceOption | None:
        return await self.session.get(PriceOption, price_option_id) if price_option_id is not None else None

    async def client(self, client_id: int) -> Client:
        return await self.session.get_one(Client, client_id)

    async def participants(self, order_id: int, lock: bool = False) -> list[tuple[Enrollment, Client]]:
        """Участники заказа (плательщик первым). lock - заблокировать их записи: после строки заказа, как при отмене."""
        stmt = (
            select(Enrollment, Client)
            .join(Client, Client.id == Enrollment.client_id)
            .where(Enrollment.order_id == order_id)
            .order_by(Enrollment.enrolled_by_client_id.is_not(None), Enrollment.id)
        )
        if lock:
            stmt = stmt.with_for_update(of=Enrollment)
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]


class AdvanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def balance(self, client_id: int) -> int:
        """Аванс клиента, в копейках: сумма строк журнала (+ зачисления, - списания)."""
        total = await self.session.scalar(select(func.sum(Advance.amount)).where(Advance.client_id == client_id))
        return int(total or 0)

    async def lock_client(self, client_id: int) -> None:
        """Аванс одного клиента меняется по очереди: два списания сразу не уведут баланс в минус."""
        await self.session.execute(select(Client.id).where(Client.id == client_id).with_for_update())

    def add(self, advance: Advance) -> None:
        self.session.add(advance)
