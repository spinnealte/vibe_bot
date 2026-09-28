from datetime import date

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.enums import EnrollmentStatus, SessionStatus
from stubbot.db.models import CourseSession, Enrollment, Program, SessionDay

# Статусы потоков, которые видны в расписании.
PUBLIC_SESSION_STATUSES = (
    SessionStatus.ANNOUNCED,
    SessionStatus.REGISTRATION_OPEN,
    SessionStatus.WAITLIST,
    SessionStatus.FULL,
    SessionStatus.REGISTRATION_CLOSED,
)
# Заявки, которые занимают места на потоке (лист ожидания мест не занимает).
SEAT_TAKING_STATUSES = (EnrollmentStatus.APPLICATION, EnrollmentStatus.CONFIRMED)


def _published_program():
    return (Program.is_published.is_(True), Program.archived_at.is_(None))


def _full_session_options():
    return (
        selectinload(CourseSession.program),
        selectinload(CourseSession.venue),
        selectinload(CourseSession.days).selectinload(SessionDay.venue),
        selectinload(CourseSession.lecturers),
        selectinload(CourseSession.price_options),
    )


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _public_sessions(self, today: date) -> Select[tuple[CourseSession]]:
        return (
            select(CourseSession)
            .join(CourseSession.program)
            .where(
                CourseSession.is_visible.is_(True),
                CourseSession.status.in_(PUBLIC_SESSION_STATUSES),
                CourseSession.end_date >= today,
                *_published_program(),
            )
        )

    async def upcoming_sessions(self, today: date, limit: int, offset: int) -> list[CourseSession]:
        stmt = (
            self._public_sessions(today)
            .options(selectinload(CourseSession.program))
            .order_by(CourseSession.start_date, CourseSession.id)
            .limit(limit)
            .offset(offset)
        )
        return list(await self.session.scalars(stmt))

    async def count_upcoming_sessions(self, today: date) -> int:
        stmt = select(func.count()).select_from(self._public_sessions(today).subquery())
        return await self.session.scalar(stmt) or 0

    async def public_session(self, session_id: int, today: date) -> CourseSession | None:
        stmt = (
            self._public_sessions(today)
            .where(CourseSession.id == session_id)
            .options(*_full_session_options())
        )
        return await self.session.scalar(stmt)

    async def published_programs(self) -> list[Program]:
        stmt = select(Program).where(*_published_program()).order_by(Program.sort_order, Program.title)
        return list(await self.session.scalars(stmt))

    async def published_program(self, program_id: int) -> Program | None:
        return await self.session.scalar(select(Program).where(Program.id == program_id, *_published_program()))

    async def program_public_sessions(self, program_id: int, today: date) -> list[CourseSession]:
        stmt = (
            self._public_sessions(today)
            .where(CourseSession.program_id == program_id)
            .order_by(CourseSession.start_date)
        )
        return list(await self.session.scalars(stmt))

    async def taken_seats(self, session_id: int) -> int:
        stmt = select(func.coalesce(func.sum(Enrollment.requested_seats), 0)).where(
            Enrollment.session_id == session_id, Enrollment.status.in_(SEAT_TAKING_STATUSES)
        )
        return int(await self.session.scalar(stmt) or 0)
