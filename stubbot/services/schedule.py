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
    seats_left: int | None  # None - число мест не ограничено

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
    """Слайд карусели расписания - проведение: с датами или без дат («даты уточняются»)."""

    program: Program
    card: SessionCard
    index: int
    total: int


class ScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.catalog = CatalogRepository(session)

    async def carousel_ids(self, today: date) -> list[int]:
        """Афиша по порядку: id проведений - с датами по дате начала, затем без дат. Их десятки - берём целиком."""
        return await self.catalog.upcoming_session_ids(today)

    async def slide(self, today: date, index: int) -> CourseSlide | None:
        """Слайд номер index (с поправкой на границы - список мог измениться). None - афиша пуста."""
        # Между запросом списка и карточки проведение могли убрать в архив - тогда список пересобирается, и на
        # этом месте оказывается соседнее. Попыток несколько: вдруг убрали не одно.
        for _ in range(3):
            ids = await self.carousel_ids(today)
            if not ids:
                logger.debug("[DB] Карусель: проведений нет")
                return None
            index = min(max(index, 0), len(ids) - 1)
            logger.debug("[DB] Карусель: слайд %d из %d (проведение #%s)", index + 1, len(ids), ids[index])
            card = await self.card(ids[index], today)
            if card is not None:
                return CourseSlide(card.program, card, index, len(ids))
        return None

    async def card(self, session_id: int, today: date) -> SessionCard | None:
        """Карточка проведения из афиши. None - проведения нет, оно в архиве или уже прошло."""
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
        """Площадки центра для «О центре» - действующие."""
        return await self.catalog.active_venues()
