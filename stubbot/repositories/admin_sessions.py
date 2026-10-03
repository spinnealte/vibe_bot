from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.models import CourseSession, Program, SessionDay, Venue


class AdminSessionRepository:
    """Проведения (sessions) для админки: все, включая скрытые и прошедшие."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self, program_id: int | None = None) -> list[CourseSession]:
        stmt = select(CourseSession).options(selectinload(CourseSession.program))
        if program_id is not None:
            stmt = stmt.where(CourseSession.program_id == program_id)
        return list(await self.session.scalars(stmt.order_by(CourseSession.start_date, CourseSession.id)))

    async def get(self, session_id: int) -> CourseSession | None:
        """Со всем, что нужно карточке «как в афише»: курс с лекторами и направлениями, площадка, дни, тарифы."""
        stmt = (
            select(CourseSession)
            .where(CourseSession.id == session_id)
            .options(
                selectinload(CourseSession.program).selectinload(Program.specialties),
                selectinload(CourseSession.program).selectinload(Program.lecturers),
                selectinload(CourseSession.venue),
                selectinload(CourseSession.days).selectinload(SessionDay.venue),
                selectinload(CourseSession.price_options),
            )
        )
        return await self.session.scalar(stmt)

    async def program(self, program_id: int) -> Program | None:
        stmt = (
            select(Program)
            .where(Program.id == program_id)
            .options(selectinload(Program.specialties), selectinload(Program.lecturers))
        )
        return await self.session.scalar(stmt)

    async def programs(self) -> list[Program]:
        return list(await self.session.scalars(select(Program).order_by(Program.sort_order, Program.title)))

    async def venues(self) -> list[Venue]:
        return list(await self.session.scalars(select(Venue).order_by(Venue.name)))

    async def venue(self, venue_id: int) -> Venue | None:
        return await self.session.get(Venue, venue_id)

    async def add(self, course_session: CourseSession) -> CourseSession:
        self.session.add(course_session)
        await self.session.flush()  # нужен id для карточки
        return course_session
