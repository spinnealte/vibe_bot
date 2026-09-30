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
        selectinload(CourseSession.program).selectinload(Program.specialties),
        selectinload(CourseSession.program).selectinload(Program.lecturers),
        selectinload(CourseSession.venue),
        selectinload(CourseSession.days).selectinload(SessionDay.venue),
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

    async def upcoming_session_ids(self, today: date) -> list[int]:
        """Порядок карусели расписания: по дате начала. Потоков десятки — список id дешевле, чем offset-запросы."""
        stmt = self._public_sessions(today).with_only_columns(CourseSession.id).order_by(
            CourseSession.start_date, CourseSession.id
        )
        return list(await self.session.scalars(stmt))

    async def public_session(self, session_id: int, today: date) -> CourseSession | None:
        stmt = (
            self._public_sessions(today)
            .where(CourseSession.id == session_id)
            .options(*_full_session_options())
        )
        return await self.session.scalar(stmt)

    async def programs_without_upcoming_ids(self, today: date) -> list[int]:
        """Опубликованные курсы, у которых нет ни одного видимого ближайшего потока («даты уточняются»)."""
        with_sessions = self._public_sessions(today).with_only_columns(CourseSession.program_id)
        stmt = (
            select(Program.id)
            .where(*_published_program(), Program.id.not_in(with_sessions))
            .order_by(Program.sort_order, Program.title)
        )
        return list(await self.session.scalars(stmt))

    async def published_program(self, program_id: int) -> Program | None:
        return await self.session.scalar(
            select(Program)
            .where(Program.id == program_id, *_published_program())
            .options(selectinload(Program.specialties), selectinload(Program.lecturers))
        )

    async def taken_seats(self, session_id: int) -> int:
        stmt = select(func.coalesce(func.sum(Enrollment.requested_seats), 0)).where(
            Enrollment.session_id == session_id, Enrollment.status.in_(SEAT_TAKING_STATUSES)
        )
        return int(await self.session.scalar(stmt) or 0)
