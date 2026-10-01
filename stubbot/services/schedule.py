import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import SessionStatus
from stubbot.db.models import CourseSession, PriceOption, Program, Venue
from stubbot.repositories.catalog import CatalogRepository

logger = logging.getLogger(__name__)

# На поток можно подать заявку; в WAITLIST/FULL она уходит в лист ожидания.
APPLY_STATUSES = (SessionStatus.REGISTRATION_OPEN, SessionStatus.WAITLIST, SessionStatus.FULL)


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
class CourseSlide:
    """Слайд карусели расписания: поток с датами или курс, у которого дат пока нет (card=None)."""

    program: Program
    card: SessionCard | None
    index: int
    total: int


class ScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.catalog = CatalogRepository(session)

    async def _carousel(self, today: date) -> list[tuple[str, int]]:
        """Порядок карусели: потоки по дате начала, затем курсы без дат. Курсов десятки — берём id целиком."""
        sessions = [("s", sid) for sid in await self.catalog.upcoming_session_ids(today)]
        programs = [("p", pid) for pid in await self.catalog.programs_without_upcoming_ids(today)]
        return sessions + programs

    async def slide(self, today: date, index: int) -> CourseSlide | None:
        """Слайд номер index (с поправкой на границы — список мог измениться). None — курсов нет совсем."""
        # Между запросом списка и карточки курс могли скрыть — тогда список пересобирается, и на этом месте
        # оказывается соседний курс. Попыток несколько: вдруг скрыли не один.
        for _ in range(3):
            items = await self._carousel(today)
            if not items:
                logger.debug("[DB] Карусель: курсов нет")
                return None
            index = min(max(index, 0), len(items) - 1)
            kind, item_id = items[index]
            logger.debug("[DB] Карусель: слайд %d из %d (%s #%s)", index + 1, len(items),
                         "поток" if kind == "s" else "курс без дат", item_id)
            if kind == "s":
                card = await self.card(item_id, today)
                if card is not None:
                    return CourseSlide(card.program, card, index, len(items))
            else:
                program = await self.catalog.published_program(item_id)
                if program is not None:
                    return CourseSlide(program, None, index, len(items))
        return None

    async def card(self, session_id: int, today: date) -> SessionCard | None:
        """Карточка публичного потока. None — потока нет, скрыт или уже прошёл."""
        course_session = await self.catalog.public_session(session_id, today)
        if course_session is None:
            return None
        seats_left = None
        if course_session.capacity is not None:
            seats_left = course_session.capacity - await self.catalog.taken_seats(course_session.id)
        return SessionCard(session=course_session, seats_left=seats_left)

    async def program(self, program_id: int) -> Program | None:
        return await self.catalog.published_program(program_id)

    async def venues(self) -> list[Venue]:
        """Площадки центра для «О центре» — действующие."""
        return await self.catalog.active_venues()
