import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import SessionStatus
from stubbot.db.models import CourseSession, PriceOption, Program, Venue
from stubbot.repositories.catalog import CatalogRepository

logger = logging.getLogger(__name__)

# На поток можно подать заявку; в WAITLIST/FULL она уходит в лист ожидания.
APPLY_STATUSES = (SessionStatus.REGISTRATION_OPEN, SessionStatus.WAITLIST, SessionStatus.FULL)


def deadline_passed(course_session: CourseSession, now: datetime | None = None) -> bool:
    """Срок «запись до» прошёл - запись закрыта, какой бы статус ни стоял у проведения."""
    deadline = course_session.registration_deadline
    return deadline is not None and (now or datetime.now(UTC)) > deadline


@dataclass(frozen=True)
class SessionCard:
    session: CourseSession
    seats_left: int | None  # None - число мест не ограничено
    # Срок записи прошёл (26.10.2026): карточка показывает «Запись закрыта» без кнопки и мест - иначе «Идёт набор»
    # висел до начала курса, а «Записаться» отвечало «запись закрыта».
    deadline_passed: bool = False

    @property
    def program(self) -> Program:
        return self.session.program

    @property
    def active_prices(self) -> list[PriceOption]:
        return [p for p in self.session.price_options if p.is_active]

    @property
    def accepts_applications(self) -> bool:
        return self.session.status in APPLY_STATUSES and not self.deadline_passed

    @property
    def shown_status(self) -> SessionStatus:
        """Статус для подписи: срок записи прошёл - «Запись закрыта», даже если в проведении стоит «Идёт набор»."""
        if self.deadline_passed and self.session.status in APPLY_STATUSES:
            return SessionStatus.REGISTRATION_CLOSED
        return self.session.status

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

    async def card(self, session_id: int, today: date, now: datetime | None = None) -> SessionCard | None:
        """Карточка проведения из афиши. None - проведения нет, оно в архиве или уже началось."""
        course_session = await self.catalog.public_session(session_id, today)
        if course_session is None:
            return None
        seats_left = None
        if course_session.capacity is not None:
            seats_left = course_session.capacity - await self.catalog.taken_seats(course_session.id)
        return SessionCard(session=course_session, seats_left=seats_left,
                           deadline_passed=deadline_passed(course_session, now))

    async def cards(self, today: date, now: datetime | None = None) -> list[SessionCard]:
        """Вся афиша карточками, в порядке карусели - для списка в веб-приложении (два запроса вместо 2N)."""
        sessions = await self.catalog.public_sessions(today)
        taken = await self.catalog.taken_seats_by_session([s.id for s in sessions if s.capacity is not None])
        now = now or datetime.now(UTC)
        return [SessionCard(session=s, seats_left=None if s.capacity is None else s.capacity - taken.get(s.id, 0),
                            deadline_passed=deadline_passed(s, now)) for s in sessions]

    async def program(self, program_id: int) -> Program | None:
        return await self.catalog.published_program(program_id)

    async def venues(self) -> list[Venue]:
        """Площадки центра для «О центре» - действующие."""
        return await self.catalog.active_venues()
