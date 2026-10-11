from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.models import Lecturer, Program, ProgramInterest, Specialty


class AdminCourseRepository:
    """Курсы (programs) для админки: действующие и архив; связи — направления и лекторы."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_active(self) -> list[Program]:
        """Раздел «Курсы»: не в архиве, по порядку (sort_order, название)."""
        stmt = select(Program).where(Program.archived_at.is_(None)).order_by(Program.sort_order, Program.title)
        return list(await self.session.scalars(stmt))

    async def list_archived(self) -> list[Program]:
        """«Архив → Курсы»: недавно убранные — первыми."""
        stmt = select(Program).where(Program.archived_at.is_not(None)).order_by(Program.archived_at.desc(), Program.id)
        return list(await self.session.scalars(stmt))

    async def archived_count(self) -> int:
        stmt = select(func.count()).select_from(Program).where(Program.archived_at.is_not(None))
        return int(await self.session.scalar(stmt) or 0)

    async def get(self, program_id: int) -> Program | None:
        stmt = (
            select(Program)
            .where(Program.id == program_id)
            .options(selectinload(Program.specialties), selectinload(Program.lecturers))
        )
        return await self.session.scalar(stmt)

    async def slug_taken(self, slug: str) -> bool:
        return await self.session.scalar(select(Program.id).where(Program.slug == slug).limit(1)) is not None

    async def specialties(self, ids: list[int] | None = None, active_only: bool = False) -> list[Specialty]:
        stmt = select(Specialty).order_by(Specialty.sort_order, Specialty.title)
        if ids is not None:
            stmt = stmt.where(Specialty.id.in_(ids))
        if active_only:
            stmt = stmt.where(Specialty.is_active.is_(True))
        return list(await self.session.scalars(stmt))

    async def lecturers(self, ids: list[int] | None = None, active_only: bool = False) -> list[Lecturer]:
        stmt = select(Lecturer).order_by(Lecturer.sort_order, Lecturer.full_name)
        if ids is not None:
            stmt = stmt.where(Lecturer.id.in_(ids))
        if active_only:
            stmt = stmt.where(Lecturer.is_active.is_(True))
        return list(await self.session.scalars(stmt))

    async def add(self, program: Program) -> Program:
        self.session.add(program)
        await self.session.flush()  # нужен id для карточки
        return program

    async def has_interests(self, program_id: int) -> bool:
        """Есть ли подписки «Сообщить о наборе» на курс — это данные клиентов, такой курс не удаляем."""
        stmt = select(ProgramInterest.id).where(ProgramInterest.program_id == program_id).limit(1)
        return await self.session.scalar(stmt) is not None

    async def delete(self, program_id: int) -> None:
        """Удалить курс навсегда; связи с направлениями и лекторами удаляет база (CASCADE).
        Проведения курса должны быть удалены раньше."""
        await self.session.execute(delete(Program).where(Program.id == program_id))
