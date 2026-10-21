"""Заказы со стороны клиента (полная версия, этап 1): кто платит и реквизиты юрлица.

Заказ создаётся при записи (services/enrollments.py). Дальше плательщик выбирает «Физлицо / Юрлицо»; юрлицо
присылает файл с реквизитами, по ним менеджер делает счёт в 1С. Счета, отметку оплаты, аванс и снятие брони
ведёт админ (шаг 4).
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import OrderStatus, PayerType
from stubbot.db.models import Order
from stubbot.repositories.orders import OrderRepository

logger = logging.getLogger(__name__)

MAX_FILE_NAME = 256  # колонка orders.requisites_file_name


class OrderService:
    def __init__(self, session: AsyncSession) -> None:
        self.orders = OrderRepository(session)

    async def payer_order(self, client_id: int, order_id: int) -> Order | None:
        """Действующий заказ, который оплачивает этот клиент. None - чужой, отменённый или такого нет."""
        order = await self.orders.get_for_payer(order_id, client_id)
        if order is None or order.status is OrderStatus.CANCELLED:
            return None
        return order

    async def choose_payer_type(self, client_id: int, order_id: int, payer_type: PayerType) -> Order | None:
        order = await self.payer_order(client_id, order_id)
        if order is None:
            return None
        order.payer_type = payer_type
        logger.info("Заказ №%s: плательщик - %s", order.number, payer_type.value)
        return order

    async def save_requisites(self, client_id: int, order_id: int, file_id: str, file_name: str | None,
                              now: datetime | None = None) -> Order | None:
        """Файл с реквизитами организации: в заказ (новый файл заменяет прежний). Счёт по нему делает менеджер."""
        order = await self.payer_order(client_id, order_id)
        if order is None:
            return None
        order.payer_type = PayerType.ORGANIZATION
        order.requisites_file_id = file_id
        order.requisites_file_name = (file_name or None) and file_name[:MAX_FILE_NAME]
        order.requisites_received_at = now or datetime.now(UTC)
        logger.info("Заказ №%s: получены реквизиты организации", order.number)
        return order
