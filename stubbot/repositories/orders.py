from datetime import date, datetime

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.enums import AdvanceReason, OrderStatus, PaymentState
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

    # --- Админка ------------------------------------------------------------------------------------------------

    async def with_payers(self, statuses: tuple[OrderStatus, ...], due_before: datetime | None = None,
                          not_past: date | None = None) -> list[tuple[Order, Client]]:
        """Заказы со статусами и их плательщики; старые первыми.

        due_before - только просроченные (срок оплаты раньше); not_past - только курсы, которые ещё не прошли
        (начало не раньше этого дня или дат нет).
        """
        stmt = (
            select(Order, Client)
            .join(Client, Client.id == Order.payer_client_id)
            .where(Order.status.in_(statuses))
            .order_by(Order.due_at, Order.id)
        )
        if due_before is not None:
            stmt = stmt.where(Order.due_at < due_before)
        if not_past is not None:
            stmt = stmt.join(CourseSession, CourseSession.id == Order.session_id).where(
                or_(CourseSession.start_date.is_(None), CourseSession.start_date >= not_past)
            )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def money_pending(self) -> list[tuple[Order, Client]]:
        """Отменённые заказы, по которым платили, а деньги ещё не распределены (ни аванса, ни возврата)."""
        paid = exists().where(Payment.order_id == Order.id, Payment.state == PaymentState.SUCCEEDED)
        stmt = (
            select(Order, Client)
            .join(Client, Client.id == Order.payer_client_id)
            .where(Order.status == OrderStatus.CANCELLED, paid, ~self._settled_clause())
            .order_by(Order.cancelled_at, Order.id)
        )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def money_settled(self, order_id: int) -> bool:
        """По отменённому заказу деньги уже распределены: в журнале аванса есть зачисление по нему."""
        return bool(await self.session.scalar(select(self._settled_clause(order_id))))

    @staticmethod
    def _settled_clause(order_id: int | None = None):
        order = Order.id if order_id is None else order_id
        return exists().where(Advance.order_id == order, Advance.reason == AdvanceReason.CANCELLED_ORDER)

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
