from datetime import datetime
from typing import Any

from sqlalchemy import delete, exists, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from stubbot.db.enums import CANCELLED_ENROLLMENT_STATUSES
from stubbot.db.models import (
    Advance,
    Client,
    Enrollment,
    Order,
    Position,
    ProgramInterest,
    Specialty,
    client_positions,
    client_specialties,
)


class ClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> Client | None:
        return await self.session.scalar(select(Client).where(Client.telegram_id == telegram_id))

    async def get_by_referral_code(self, code: str) -> Client | None:
        return await self.session.scalar(
            select(Client).where(Client.referral_code == code, Client.deleted_at.is_(None))
        )

    async def get_by_phone(self, phone: str) -> Client | None:
        return await self.session.scalar(select(Client).where(Client.phone == phone, Client.deleted_at.is_(None)))

    async def close_merged(self, ids: list[int], into_id: int, now: datetime) -> None:
        """Профили, чьи записи перешли клиенту into_id: закрыты (как удалённые - из поиска и выгрузки уходят,
        телефон освобождается), в заметке - с кем объединены. Строки остаются для истории."""
        await self.session.execute(
            update(Client).where(Client.id.in_(ids))
            .values(deleted_at=now, admin_note=func.concat_ws("\n", Client.admin_note,
                                                               f"Объединён с клиентом #{into_id}"))
        )

    async def all_by_phone(self, phone: str) -> list[Client]:
        """Действующие профили с этим телефоном - не больше одного (уникальный индекс), список - для простоты."""
        stmt = select(Client).where(Client.phone == phone, Client.deleted_at.is_(None)).order_by(Client.id)
        return list(await self.session.scalars(stmt))

    async def records_overlap(self, from_ids: list[int], to_id: int) -> bool:
        """Есть ли у обоих неотменённая запись на одно и то же проведение - тогда объединять нельзя."""
        theirs = aliased(Enrollment)
        stmt = select(exists().where(
            Enrollment.client_id.in_(from_ids),
            Enrollment.status.not_in(CANCELLED_ENROLLMENT_STATUSES),
            exists().where(theirs.client_id == to_id, theirs.session_id == Enrollment.session_id,
                           theirs.status.not_in(CANCELLED_ENROLLMENT_STATUSES)),
        ))
        return bool(await self.session.scalar(stmt))

    async def move_records(self, from_ids: list[int], to_id: int) -> None:
        """Записи, заказы, аванс и листы ожидания курсов профилей from_ids - клиенту to_id. Подписка на тот же
        курс у него уже есть - вторая не нужна."""
        for column in (Enrollment.client_id, Enrollment.enrolled_by_client_id):
            await self.session.execute(update(Enrollment).where(column.in_(from_ids)).values({column.key: to_id}))
        await self.session.execute(update(Order).where(Order.payer_client_id.in_(from_ids))
                                   .values(payer_client_id=to_id))
        await self.session.execute(update(Advance).where(Advance.client_id.in_(from_ids)).values(client_id=to_id))
        own = select(ProgramInterest.program_id).where(ProgramInterest.client_id == to_id)
        await self.session.execute(delete(ProgramInterest).where(ProgramInterest.client_id.in_(from_ids),
                                                                 ProgramInterest.program_id.in_(own)))
        await self.session.execute(update(ProgramInterest).where(ProgramInterest.client_id.in_(from_ids))
                                   .values(client_id=to_id))

    async def add(self, client: Client) -> Client:
        self.session.add(client)
        await self.session.flush()  # нужен id для записи на курс
        return client

    async def insert_if_absent(self, values: dict[str, Any]) -> Client | None:
        """INSERT … ON CONFLICT DO NOTHING RETURNING. None - строка не вставлена (гонка двух /start или коллизия кода)."""
        stmt = pg_insert(Client).values(**values).on_conflict_do_nothing().returning(Client)
        return await self.session.scalar(stmt)

    async def get_with_profile(self, client_id: int) -> Client:
        stmt = (
            select(Client)
            .where(Client.id == client_id)
            .options(selectinload(Client.specialties), selectinload(Client.positions))
            .execution_options(populate_existing=True)
        )
        return (await self.session.scalars(stmt)).one()

    async def all_for_export(self) -> list[Client]:
        """Все клиенты (кроме удалённых) со специальностями и должностями - для выгрузки, в порядке прихода."""
        stmt = (
            select(Client)
            .where(Client.deleted_at.is_(None))
            .options(selectinload(Client.specialties), selectinload(Client.positions))
            .order_by(Client.id)
            .execution_options(populate_existing=True)
        )
        return list(await self.session.scalars(stmt))

    async def has_specialties(self, client_id: int) -> bool:
        return bool(await self.session.scalar(select(exists().where(client_specialties.c.client_id == client_id))))

    async def has_positions(self, client_id: int) -> bool:
        return bool(await self.session.scalar(select(exists().where(client_positions.c.client_id == client_id))))

    async def specialty_ids(self, client_id: int) -> list[int]:
        rows = await self.session.scalars(
            select(client_specialties.c.specialty_id).where(client_specialties.c.client_id == client_id)
        )
        return list(rows)

    async def position_ids(self, client_id: int) -> list[int]:
        rows = await self.session.scalars(
            select(client_positions.c.position_id).where(client_positions.c.client_id == client_id)
        )
        return list(rows)

    async def replace_specialties(self, client_id: int, specialty_ids: list[int]) -> None:
        await self.session.execute(delete(client_specialties).where(client_specialties.c.client_id == client_id))
        if specialty_ids:
            await self.session.execute(
                insert(client_specialties),
                [{"client_id": client_id, "specialty_id": sid} for sid in specialty_ids],
            )

    async def replace_positions(self, client_id: int, position_ids: list[int]) -> None:
        await self.session.execute(delete(client_positions).where(client_positions.c.client_id == client_id))
        if position_ids:
            await self.session.execute(
                insert(client_positions),
                [{"client_id": client_id, "position_id": pid} for pid in position_ids],
            )

    async def mark_bot_blocked(self, telegram_id: int, at: datetime) -> int | None:
        """Отметить, что клиент заблокировал бота. Возвращает id клиента; None - не найден или уже отмечен."""
        return await self.session.scalar(
            update(Client)
            .where(Client.telegram_id == telegram_id, Client.is_bot_blocked.is_(False))
            .values(is_bot_blocked=True, bot_blocked_at=at)
            .returning(Client.id)
        )

    async def count_referrals(self, client_id: int) -> int:
        return await self.session.scalar(
            select(func.count()).select_from(Client).where(Client.referred_by_client_id == client_id)
        ) or 0


class DictionaryRepository:
    """Справочники специальностей и должностей: только активные, в порядке sort_order."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active_specialties(self) -> list[Specialty]:
        stmt = select(Specialty).where(Specialty.is_active).order_by(Specialty.sort_order, Specialty.title)
        return list(await self.session.scalars(stmt))

    async def active_positions(self) -> list[Position]:
        stmt = select(Position).where(Position.is_active).order_by(Position.sort_order, Position.title)
        return list(await self.session.scalars(stmt))
