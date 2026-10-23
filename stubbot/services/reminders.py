"""Напоминания об оплате (полная версия, этап 1, шаг 5; решения 20-22.10.2026).

Только неоплаченные заказы (частичная оплата уже закрепила место - остаток ведёт менеджер) и только пока курс
не начался:
- через сутки после записи - «напоминание об оплате» клиенту и админам;
- когда вышел срок оплаты (72 часа) - клиенту «срок истёк, менеджер может снять бронь», админам «просрочено»
  с кнопкой «Снять бронь». Сам бот бронь не снимает.
Без «тихих часов»: напоминание приходит ровно через сутки и через 72 часа - в то же время суток, когда человек
записывался, то есть когда он обычно на связи (решение 22.10.2026).

Отметка ставится в той же транзакции, где заказ выбран под блокировкой, - до отправки: после перезапуска бота
напоминание не придёт второй раз (в худшем случае одно потеряется). Бот был выключен дольше срока оплаты - уйдёт
только «просрочено», без устаревшего «сутки без оплаты».
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.models import Client, CourseSession, Order
from stubbot.repositories.orders import OrderRepository

REMIND_AFTER = timedelta(hours=24)
BATCH = 50  # заказов за одну проверку каждого вида; остальные - через интервал


class ReminderKind(StrEnum):
    DAY = "day"  # сутки без оплаты
    OVERDUE = "overdue"  # срок оплаты вышел


@dataclass(frozen=True)
class ReminderJob:
    kind: ReminderKind
    order: Order
    session: CourseSession  # с курсом и днями - для названия и дат
    payer: Client
    participants: int  # 2 - записывались вдвоём с коллегой


class ReminderService:
    def __init__(self, session: AsyncSession) -> None:
        self.orders = OrderRepository(session)

    async def claim(self, now: datetime, today: date) -> list[ReminderJob]:
        """Заказы, которым пора напомнить, - сразу с отметкой. today - сегодня по часовому поясу центра."""
        jobs = []
        for order in await self.orders.lock_overdue(now, today, BATCH):
            order.overdue_notified_at = now
            if order.payment_reminded_at is None:
                order.payment_reminded_at = now  # «сутки без оплаты» уже устарело
            jobs.append(await self._job(ReminderKind.OVERDUE, order))
        for order in await self.orders.lock_unreminded(now - REMIND_AFTER, now, today, BATCH):
            order.payment_reminded_at = now
            jobs.append(await self._job(ReminderKind.DAY, order))
        return jobs

    async def _job(self, kind: ReminderKind, order: Order) -> ReminderJob:
        return ReminderJob(
            kind=kind,
            order=order,
            session=await self.orders.session_for_card(order.session_id),
            payer=await self.orders.client(order.payer_client_id),
            participants=len(await self.orders.participants(order.id)),
        )
