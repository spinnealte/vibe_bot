from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import SessionStatus
from stubbot.db.models import CourseSession, PriceOption, Program
from stubbot.repositories.catalog import CatalogRepository

# На поток можно подать заявку; в WAITLIST/FULL она уходит в лист ожидания.
APPLY_STATUSES = (SessionStatus.REGISTRATION_OPEN, SessionStatus.WAITLIST, SessionStatus.FULL)
PAGE_SIZE = 6


@dataclass(frozen=True)
class SessionCard:
    session: CourseSession
    seats_left: int | None  # None — число мест не ограничено

    @property
    def program(self) -> Program:
        return self.session.program

    @property
    def active_prices(self) -> list[PriceOption]:
        return [p for p in self.session.price_options if p.is_active]

    @property
    def accepts_applications(self) -> bool:
        return self.session.status in APPLY_STATUSES

    @property
    def goes_to_waitlist(self) -> bool:
        """Новая заявка сразу попадёт в лист ожидания."""
        if self.session.status is not SessionStatus.REGISTRATION_OPEN:
            return True
        return self.seats_left is not None and self.seats_left <= 0


@dataclass(frozen=True)
class SchedulePage:
    sessions: list[CourseSession]
    page: int
    pages: int


class ScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.catalog = CatalogRepository(session)

    async def page(self, today: date, page: int) -> SchedulePage:
        total = await self.catalog.count_upcoming_sessions(today)
        pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
        page = min(max(page, 0), pages - 1)
        sessions = await self.catalog.upcoming_sessions(today, PAGE_SIZE, page * PAGE_SIZE)
        return SchedulePage(sessions=sessions, page=page, pages=pages)

    async def card(self, session_id: int, today: date) -> SessionCard | None:
        """Карточка публичного потока. None — потока нет, скрыт или уже прошёл."""
        course_session = await self.catalog.public_session(session_id, today)
        if course_session is None:
            return None
        seats_left = None
        if course_session.capacity is not None:
            seats_left = course_session.capacity - await self.catalog.taken_seats(course_session.id)
        return SessionCard(session=course_session, seats_left=seats_left)

    async def programs(self) -> list[Program]:
        return await self.catalog.published_programs()

    async def program(self, program_id: int) -> Program | None:
        return await self.catalog.published_program(program_id)

    async def program_sessions(self, program_id: int, today: date) -> list[CourseSession]:
        return await self.catalog.program_public_sessions(program_id, today)
