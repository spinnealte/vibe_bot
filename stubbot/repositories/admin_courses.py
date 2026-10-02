from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.models import Lecturer, Program, Specialty


class AdminCourseRepository:
    """Курсы (programs) для админки: все, включая архив; связи — направления и лекторы."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[Program]:
        """Действующие сначала, архив — в конце; внутри — как в афише (sort_order, название)."""
        stmt = select(Program).order_by(Program.archived_at.is_not(None), Program.sort_order, Program.title)
        return list(await self.session.scalars(stmt))

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
