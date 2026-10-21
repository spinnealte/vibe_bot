from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import PaymentState
from stubbot.db.models import Advance, Order, Payment


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, order: Order) -> Order:
        self.session.add(order)
        await self.session.flush()  # номер заказа выдаёт база - он нужен сразу
        return order

    async def get_for_payer(self, order_id: int, client_id: int) -> Order | None:
        """Только свой заказ: id из кнопки может быть подделан."""
        return await self.session.scalar(select(Order).where(Order.id == order_id, Order.payer_client_id == client_id))

    async def lock(self, order_id: int) -> Order | None:
        """Заказ с блокировкой строки до конца транзакции: отмена клиентом и отметка оплаты админом не пересекутся
        (иначе отмена «без оплаты» может проскочить мимо только что внесённых денег)."""
        return await self.session.scalar(select(Order).where(Order.id == order_id).with_for_update())

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


class AdvanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def balance(self, client_id: int) -> int:
        """Аванс клиента, в копейках: сумма строк журнала (+ зачисления, - списания)."""
        total = await self.session.scalar(select(func.sum(Advance.amount)).where(Advance.client_id == client_id))
        return int(total or 0)
