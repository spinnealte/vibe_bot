from datetime import datetime
from typing import Any

from sqlalchemy import delete, exists, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.models import Client, Position, Specialty, client_positions, client_specialties


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

    async def insert_if_absent(self, values: dict[str, Any]) -> Client | None:
        """INSERT … ON CONFLICT DO NOTHING RETURNING. None — строка не вставлена (гонка двух /start или коллизия кода)."""
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
        """Отметить, что клиент заблокировал бота. Возвращает id клиента; None — не найден или уже отмечен."""
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
