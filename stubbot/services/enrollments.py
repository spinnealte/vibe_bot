"""Запись на курс (полная версия, этап 1; решения 19-20.10.2026).

«Записаться» сразу занимает место и создаёт заказ: сумма фиксируется по тарифу в момент записи, оплатить нужно
за 72 часа (PAYMENT_HOURS). Записаться можно за себя или вдвоём с коллегой по тарифу «Для двух коллег» (цена за
двоих, платит записывающий): коллега - отдельный клиент, у него своя запись по тому же заказу. Мест не хватает -
лист ожидания (обоих, если записываются вдвоём), заказа нет.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import ClientEventType, EnrollmentSource, EnrollmentStatus, PriceUnit
from stubbot.db.models import Client, CourseSession, Enrollment, Order, PriceOption, Program
from stubbot.db.models.orders import PAYMENT_HOURS
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.repositories.enrollments import EnrollmentRepository
from stubbot.repositories.orders import OrderRepository
from stubbot.services.clients import ClientService
from stubbot.services.schedule import ScheduleService, SessionCard

logger = logging.getLogger(__name__)

PAIR_SEATS = 2  # «Для двух коллег»: записывающий и его коллега
# Клиент может сам отменить только ещё не подтверждённую заявку; подтверждённую - через менеджера.
CLIENT_CANCELLABLE = (EnrollmentStatus.APPLICATION, EnrollmentStatus.WAITLIST)
# Не показываем в «Моих заявках».
HIDDEN_FROM_CLIENT = (EnrollmentStatus.CANCELLED_BY_CLIENT,)


@dataclass(frozen=True)
class MyApplication:
    enrollment: Enrollment
    session: CourseSession
    program: Program
    price: PriceOption | None

    @property
    def title(self) -> str:
        return self.session.title_override or self.program.title

    @property
    def cancellable(self) -> bool:
        return self.enrollment.status in CLIENT_CANCELLABLE


@dataclass(frozen=True)
class Colleague:
    """Коллега для тарифа «Для двух коллег»: полные ФИО и телефон в E.164 - их вводит записывающий."""

    full_name: str
    phone: str


class EnrollOutcome(StrEnum):
    CREATED = "created"  # места заняты, заказ создан
    WAITLIST = "waitlist"  # мест не хватило - лист ожидания, заказа нет
    DUPLICATE = "duplicate"  # записывающий уже записан на это проведение
    COLLEAGUE_DUPLICATE = "colleague_duplicate"  # коллега уже записан на это проведение
    COLLEAGUE_IS_SELF = "colleague_is_self"  # телефон коллеги - телефон самого записывающего
    CLOSED = "closed"  # проведение не найдено, в архиве, прошло, запись закрыта или истёк срок записи
    BAD_PRICE = "bad_price"  # тариф не этого проведения; «для двух коллег» без коллеги или коллега к другому тарифу
    BUSY = "busy"  # параллельная запись того же человека или коллеги - попробовать ещё раз


@dataclass(frozen=True)
class EnrollResult:
    outcome: EnrollOutcome
    card: SessionCard | None = None
    enrollment: Enrollment | None = None  # запись записывающего (при DUPLICATE - уже существующая)
    order: Order | None = None
    colleague: Client | None = None


def is_pair_price(price: PriceOption | None) -> bool:
    """Тариф «Для двух коллег»: цена за двоих, оба места - в одном заказе."""
    return (price is not None and price.unit is PriceUnit.PER_GROUP
            and price.min_seats == PAIR_SEATS and price.max_seats == PAIR_SEATS)


class EnrollmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.db = session
        self.enrollments = EnrollmentRepository(session)
        self.orders = OrderRepository(session)
        self.clients = ClientService(session)
        self.analytics = AnalyticsRepository(session)
        self.schedule = ScheduleService(session)

    async def availability(self, client: Client, session_id: int, today: date) -> tuple[SessionCard | None, Enrollment | None]:
        """Карточка потока (None - записаться нельзя) и уже существующая активная запись клиента."""
        card = await self.schedule.card(session_id, today)
        if card is None or not card.accepts_applications or self._deadline_passed(card.session):
            return None, None
        return card, await self.enrollments.active_for(client.id, session_id)

    async def enroll(self, client: Client, session_id: int, price_option_id: int | None, colleague: Colleague | None,
                     today: date, now: datetime | None = None) -> EnrollResult:
        """Записать клиента (и коллегу) на проведение: занять места и создать заказ - или лист ожидания."""
        now = now or datetime.now(UTC)
        # Места считаем под блокировкой проведения: двое не займут одно последнее место одновременно.
        await self.enrollments.lock_session(session_id)
        card, existing = await self.availability(client, session_id, today)
        if card is None:
            return EnrollResult(EnrollOutcome.CLOSED)
        if existing is not None:
            return EnrollResult(EnrollOutcome.DUPLICATE, card, existing)

        price = None
        if price_option_id is not None:
            price = next((p for p in card.active_prices if p.id == price_option_id), None)
            if price is None:
                return EnrollResult(EnrollOutcome.BAD_PRICE, card)
        elif card.active_prices:
            return EnrollResult(EnrollOutcome.BAD_PRICE, card)  # тарифы есть - без выбора записаться нельзя
        pair = is_pair_price(price)
        if pair != (colleague is not None):
            return EnrollResult(EnrollOutcome.BAD_PRICE, card)

        colleague_client = None
        if colleague is not None:
            if colleague.phone == client.phone:
                return EnrollResult(EnrollOutcome.COLLEAGUE_IS_SELF, card)
            colleague_client = await self.clients.find_by_phone(colleague.phone)
            if colleague_client is not None and colleague_client.id == client.id:
                return EnrollResult(EnrollOutcome.COLLEAGUE_IS_SELF, card)
            if colleague_client is not None and await self.enrollments.active_for(colleague_client.id, session_id):
                return EnrollResult(EnrollOutcome.COLLEAGUE_DUPLICATE, card)

        seats = PAIR_SEATS if pair else 1
        waitlist = card.goes_to_waitlist or (card.seats_left is not None and card.seats_left < seats)
        order = None
        try:
            # Точка сохранения: если упрёмся в уникальность (двойное нажатие, параллельная запись), не останется
            # ни заказа, ни записей, ни нового клиента-коллеги - а транзакция апдейта живёт дальше.
            async with self.db.begin_nested():
                if colleague is not None and colleague_client is None:
                    colleague_client = await self.clients.add_offline(colleague.full_name, colleague.phone)
                if not waitlist:
                    order = await self.orders.add(Order(
                        payer_client_id=client.id,
                        session_id=session_id,
                        price_option_id=price.id if price else None,
                        total_amount=price.amount if price else None,  # цена закрепляется в момент записи
                        due_at=now + timedelta(hours=PAYMENT_HOURS),
                    ))
                participants = [client] + ([colleague_client] if colleague_client else [])
                enrollments = [Enrollment(
                    client_id=person.id,
                    session_id=session_id,
                    price_option_id=price.id if price else None,
                    order_id=order.id if order else None,
                    status=EnrollmentStatus.WAITLIST if waitlist else EnrollmentStatus.APPLICATION,
                    requested_seats=1,
                    source=EnrollmentSource.BOT,
                ) for person in participants]
                for enrollment in enrollments:
                    self.enrollments.add(enrollment)
                await self.db.flush()
        except IntegrityError:
            logger.info("Клиент #%s: запись на поток #%s отклонена базой (повтор или параллельная запись)",
                        client.id, session_id)
            mine = await self.enrollments.active_for(client.id, session_id)
            if mine is not None:
                return EnrollResult(EnrollOutcome.DUPLICATE, card, mine)
            return EnrollResult(EnrollOutcome.BUSY, card)

        self.analytics.add_event(client.id, ClientEventType.APPLICATION_CREATED, {
            "session_id": session_id, "status": enrollments[0].status.value, "pair": pair,
            "order": order.number if order else None,
        })
        logger.info("Клиент #%s: запись на поток #%s - %s%s%s", client.id, session_id, enrollments[0].status.value,
                    f", заказ №{order.number}" if order else "",
                    f", с коллегой #{colleague_client.id}" if colleague_client else "")
        return EnrollResult(EnrollOutcome.WAITLIST if waitlist else EnrollOutcome.CREATED, card, enrollments[0],
                            order, colleague_client)

    async def my_applications(self, client_id: int) -> list[MyApplication]:
        """Заявки для «Мои заявки». Отменённые самим клиентом не показываем - он их уже убрал."""
        rows = await self.enrollments.list_for_client(client_id, exclude_statuses=HIDDEN_FROM_CLIENT)
        return [MyApplication(*row) for row in rows]

    async def cancel(self, client_id: int, enrollment_id: int) -> Enrollment | None:
        """None - не своя заявка или её уже нельзя отменить самому."""
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
