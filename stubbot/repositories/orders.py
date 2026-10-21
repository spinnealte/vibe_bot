from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.models import Order


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
