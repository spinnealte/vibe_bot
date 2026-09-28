import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import (
    ClientEventType,
    EnrollmentSource,
    EnrollmentStatus,
    PriceUnit,
)
from stubbot.db.models import Client, CourseSession, Enrollment, PriceOption, Program
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.repositories.enrollments import EnrollmentRepository
from stubbot.services.schedule import ScheduleService, SessionCard

logger = logging.getLogger(__name__)

MAX_COMMENT_LENGTH = 1000
MAX_SEATS = 10
DEFAULT_MAX_SEATS = 5  # если тариф не ограничивает состав: «я + до 4 коллег», больше — через комментарий
# Клиент может сам отменить только ещё не подтверждённую заявку; подтверждённую — через менеджера.
CLIENT_CANCELLABLE = (EnrollmentStatus.APPLICATION, EnrollmentStatus.WAITLIST)


class ApplyOutcome(StrEnum):
    CREATED = "created"
    WAITLIST = "waitlist"
    DUPLICATE = "duplicate"
    CLOSED = "closed"  # поток не найден, скрыт, прошёл, запись закрыта или истёк дедлайн
    BAD_PRICE = "bad_price"


@dataclass(frozen=True)
class ApplyResult:
    outcome: ApplyOutcome
    enrollment: Enrollment | None = None


def fixed_group_seats(price: PriceOption | None) -> int | None:
    """Тариф «на группу» с фиксированным составом («для двух коллег») сам задаёт число мест."""
    if price and price.unit is PriceUnit.PER_GROUP and price.max_seats and price.min_seats == price.max_seats:
        return price.min_seats
    return None


def seat_choices(price: PriceOption | None) -> list[int]:
    low = price.min_seats if price else 1
    high = min(price.max_seats or DEFAULT_MAX_SEATS, MAX_SEATS) if price else DEFAULT_MAX_SEATS
    return list(range(low, max(high, low) + 1))


class EnrollmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.db = session
        self.enrollments = EnrollmentRepository(session)
        self.analytics = AnalyticsRepository(session)
        self.schedule = ScheduleService(session)

    async def availability(self, client: Client, session_id: int, today: date) -> tuple[SessionCard | None, Enrollment | None]:
        """Карточка потока (None — подать нельзя) и уже существующая активная заявка клиента."""
        card = await self.schedule.card(session_id, today)
        if card is None or not card.accepts_applications or self._deadline_passed(card.session):
            return None, None
        return card, await self.enrollments.active_for(client.id, session_id)

    async def apply(self, client: Client, session_id: int, price_option_id: int | None, seats: int,
                    comment: str | None, today: date) -> ApplyResult:
        card, existing = await self.availability(client, session_id, today)
        if card is None:
            return ApplyResult(ApplyOutcome.CLOSED)
        if existing is not None:
            return ApplyResult(ApplyOutcome.DUPLICATE, existing)

        price = None
        if price_option_id is not None:
            price = next((p for p in card.active_prices if p.id == price_option_id), None)
            if price is None:
                return ApplyResult(ApplyOutcome.BAD_PRICE)
        seats = fixed_group_seats(price) or seats
        if seats not in seat_choices(price):
            return ApplyResult(ApplyOutcome.BAD_PRICE)

        waitlist = card.goes_to_waitlist or (card.seats_left is not None and card.seats_left < seats)
        enrollment = Enrollment(
            client_id=client.id,
            session_id=session_id,
            price_option_id=price.id if price else None,
            status=EnrollmentStatus.WAITLIST if waitlist else EnrollmentStatus.APPLICATION,
            requested_seats=seats,
            source=EnrollmentSource.BOT,
            client_comment=(comment or None) and comment[:MAX_COMMENT_LENGTH],
        )
        try:
            # Savepoint: параллельная заявка (двойное нажатие) упрётся в частичный UNIQUE — не роняем транзакцию.
            async with self.db.begin_nested():
                self.enrollments.add(enrollment)
        except IntegrityError:
            logger.info("Клиент #%s: повторная заявка на поток #%s отклонена базой", client.id, session_id)
            return ApplyResult(ApplyOutcome.DUPLICATE, await self.enrollments.active_for(client.id, session_id))

        self.analytics.add_event(
            client.id,
            ClientEventType.APPLICATION_CREATED,
            {"session_id": session_id, "status": enrollment.status.value, "seats": seats},
        )
        logger.info("Клиент #%s: заявка #%s на поток #%s — %s, мест %s", client.id, enrollment.id, session_id,
                    enrollment.status.value, seats)
        return ApplyResult(ApplyOutcome.WAITLIST if waitlist else ApplyOutcome.CREATED, enrollment)

    async def my_applications(self, client_id: int) -> list[tuple[Enrollment, CourseSession, Program]]:
        return await self.enrollments.list_for_client(client_id)

    async def cancel(self, client_id: int, enrollment_id: int) -> Enrollment | None:
        """None — не своя заявка или её уже нельзя отменить самому."""
        enrollment = await self.enrollments.get_for_client(enrollment_id, client_id)
        if enrollment is None or enrollment.status not in CLIENT_CANCELLABLE:
            return None
        enrollment.status = EnrollmentStatus.CANCELLED_BY_CLIENT
        enrollment.cancelled_at = datetime.now(UTC)
        enrollment.cancel_reason = "Отменена клиентом в боте"
        logger.info("Клиент #%s: отменил заявку #%s", client_id, enrollment_id)
        return enrollment

    async def subscribe_interest(self, client_id: int, program_id: int) -> bool:
        created = await self.enrollments.add_interest(client_id, program_id)
        if created:
            self.analytics.add_event(client_id, ClientEventType.INTEREST_CREATED, {"program_id": program_id})
            logger.info("Клиент #%s: подписался на набор курса #%s", client_id, program_id)
        return created

    @staticmethod
    def _deadline_passed(course_session: CourseSession) -> bool:
        deadline = course_session.registration_deadline
        return deadline is not None and datetime.now(UTC) > deadline
