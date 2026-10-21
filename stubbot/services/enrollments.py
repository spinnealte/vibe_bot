"""Запись на курс и её отмена самим клиентом (полная версия, этап 1; решения 19-21.10.2026).

«Записаться» сразу занимает место и создаёт заказ: сумма фиксируется по тарифу в момент записи, оплатить нужно
за 72 часа (PAYMENT_HOURS). Записаться можно за себя или вдвоём с коллегой по тарифу «Для двух коллег» (цена за
двоих, платит записывающий): коллега - отдельный клиент, у него своя запись по тому же заказу. Мест не хватает -
лист ожидания (обоих, если записываются вдвоём), заказа нет.

Отменить запись сам клиент может не позже чем за 2 недели до начала (у проведения без дат - всегда), позже - через
менеджера. Пара отменяется целиком и только записавшим; коллега отказывается через менеджера. Оплаты не было -
заказ закрывается, оба попадают в лист ожидания курса. Была - клиент выбирает: перенос, аванс или возврат, деньги
двигает админ.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import CancelChoice, ClientEventType, EnrollmentSource, EnrollmentStatus, OrderStatus, PriceUnit
from stubbot.db.models import Client, CourseSession, Enrollment, Order, PriceOption, Program
from stubbot.db.models.orders import PAYMENT_HOURS
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.repositories.enrollments import EnrollmentRepository
from stubbot.repositories.orders import OrderRepository
from stubbot.services.clients import ClientService
from stubbot.services.schedule import ScheduleService, SessionCard

logger = logging.getLogger(__name__)

PAIR_SEATS = 2  # «Для двух коллег»: записывающий и его коллега
SELF_CANCEL_DAYS = 14  # сам клиент отменяет запись не позже чем за 2 недели до начала (решение 20.10.2026)
# Запись, которую можно отменить (или выйти из листа ожидания); прошедшие и отменённые - нет.
CLIENT_CANCELLABLE = (EnrollmentStatus.APPLICATION, EnrollmentStatus.CONFIRMED, EnrollmentStatus.WAITLIST)
# Не показываем в «Моих записях».
HIDDEN_FROM_CLIENT = (EnrollmentStatus.CANCELLED_BY_CLIENT,)
# Заказ ждёт денег: срок оплаты, «Как будете оплачивать?», реквизиты.
PAYABLE_ORDER_STATUSES = (OrderStatus.AWAITING_PAYMENT, OrderStatus.PARTIALLY_PAID)
CLIENT_CANCEL_REASON = "Отменена клиентом в боте"


def cancel_deadline(course_session: CourseSession) -> date | None:
    """Последний день, когда клиент отменяет запись сам (включительно). None - у проведения нет дат: можно всегда."""
    if course_session.start_date is None:
        return None
    return course_session.start_date - timedelta(days=SELF_CANCEL_DAYS)


def can_self_cancel(enrollment: Enrollment, course_session: CourseSession, today: date) -> bool:
    """Отменить здесь, без менеджера: только свою запись (не коллегу из пары) и не позже чем за 2 недели до начала.
    Из листа ожидания выйти можно всегда - места и денег там нет."""
    if enrollment.status not in CLIENT_CANCELLABLE or enrollment.enrolled_by_client_id is not None:
        return False
    if enrollment.status is EnrollmentStatus.WAITLIST:
        return True
    deadline = cancel_deadline(course_session)
    return deadline is None or today <= deadline


@dataclass(frozen=True)
class MyApplication:
    """Карточка «Моих записей»: запись, проведение, заказ (у листа ожидания нет) и второй участник пары."""

    enrollment: Enrollment
    session: CourseSession
    program: Program
    price: PriceOption | None
    order: Order | None = None
    paid: int = 0  # оплачено по заказу, копейки
    partner: Client | None = None  # коллега, которого записал клиент, или тот, кто записал клиента
    self_cancel: bool = False  # можно отменить здесь (can_self_cancel)

    @property
    def title(self) -> str:
        return self.session.title_override or self.program.title

    @property
    def is_payer(self) -> bool:
        """Записался сам (и платит), а не записан коллегой."""
        return self.enrollment.enrolled_by_client_id is None

    @property
    def active(self) -> bool:
        return self.enrollment.status in CLIENT_CANCELLABLE

    @property
    def cancel_until(self) -> date | None:
        return cancel_deadline(self.session)


class CancelOutcome(StrEnum):
    CANCELLED = "cancelled"
    NEEDS_CHOICE = "needs_choice"  # по заказу уже платили: сначала выбрать перенос, аванс или возврат
    NOT_ALLOWED = "not_allowed"  # чужая или уже отменённая, коллега из пары, до начала меньше двух недель


@dataclass(frozen=True)
class CancelResult:
    outcome: CancelOutcome
    enrollment: Enrollment | None = None
    order: Order | None = None
    paid: int = 0
    choice: CancelChoice | None = None
    colleagues: tuple[Client, ...] = ()  # их записи отменены вместе с записью клиента
    course_waitlist: bool = False  # заказ без оплаты: все участники в листе ожидания курса
    waitlist: bool = False  # это был выход из листа ожидания проведения


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
                    # Пару связывает «кто записал» - и в листе ожидания, где заказа нет.
                    enrolled_by_client_id=client.id if person is not client else None,
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

    async def my_applications(self, client_id: int, today: date) -> list[MyApplication]:
        """Записи для «Моих записей». Отменённые самим клиентом не показываем - он их уже убрал."""
        rows = await self.enrollments.list_for_client(client_id, exclude_statuses=HIDDEN_FROM_CLIENT)
        paid = await self.orders.paid_amounts([order.id for *_, order in rows if order is not None])
        partners = await self.enrollments.partners(client_id, [enrollment.session_id for enrollment, *_ in rows])
        return [
            MyApplication(enrollment, course_session, program, price, order,
                          paid=paid.get(order.id, 0) if order is not None else 0,
                          partner=partners.get(course_session.id),
                          self_cancel=can_self_cancel(enrollment, course_session, today))
            for enrollment, course_session, program, price, order in rows
        ]

    async def cancel(self, client_id: int, enrollment_id: int, today: date, choice: CancelChoice | None = None,
                     now: datetime | None = None) -> CancelResult:
        """Отмена записи самим клиентом (вместе с коллегой, если записывались вдвоём).

        По заказу уже платили, а выбора (перенос / аванс / возврат) нет - NEEDS_CHOICE, ничего не меняем.
        """
        enrollment = await self.enrollments.get_for_client(enrollment_id, client_id)
        if enrollment is None:
            return CancelResult(CancelOutcome.NOT_ALLOWED)
        # Сначала заказ, потом запись - в том же порядке блокирует и отметка оплаты админом: пока идёт отмена,
        # деньги по заказу не появятся, а второе нажатие «Отменить» дождётся первого и увидит отменённую запись.
        order = await self.orders.lock(enrollment.order_id) if enrollment.order_id is not None else None
        enrollment = await self.enrollments.get_for_client(enrollment_id, client_id, lock=True)
        course_session = await self.enrollments.get_session(enrollment.session_id)
        if not can_self_cancel(enrollment, course_session, today):
            return CancelResult(CancelOutcome.NOT_ALLOWED, enrollment)

        paid = (await self.orders.paid_amounts([order.id])).get(order.id, 0) if order is not None else 0
        if paid and choice is None:
            return CancelResult(CancelOutcome.NEEDS_CHOICE, enrollment, order, paid)
        if not paid:
            choice = None  # денег нет - переносить и возвращать нечего

        now = now or datetime.now(UTC)
        waitlist = enrollment.status is EnrollmentStatus.WAITLIST
        colleagues = await self.enrollments.colleagues(enrollment.session_id, client_id)
        for row in [enrollment, *(row for row, _ in colleagues)]:
            row.status = EnrollmentStatus.CANCELLED_BY_CLIENT
            row.cancelled_at = now
            row.cancel_reason = CLIENT_CANCEL_REASON
        course_waitlist = order is not None and not paid
        if order is not None:
            order.status = OrderStatus.CANCELLED
            order.cancelled_at = now
            order.cancel_reason = CLIENT_CANCEL_REASON
            order.cancel_choice = choice
        if course_waitlist:
            # Не оплачено - в лист ожидания курса (решение 20.10.2026): на новое проведение пригласит админ.
            for person_id in [client_id, *(person.id for _, person in colleagues)]:
                await self.enrollments.put_on_course_waitlist(person_id, course_session.program_id)

        self.analytics.add_event(client_id, ClientEventType.APPLICATION_CANCELLED, {
            "session_id": enrollment.session_id, "order": order.number if order else None,
            "paid": bool(paid), "choice": choice.value if choice else None, "pair": bool(colleagues),
        })
        logger.info("Клиент #%s: отменил запись #%s%s%s", client_id, enrollment.id,
                    f", заказ №{order.number}" if order else "", f", выбор - {choice.value}" if choice else "")
        return CancelResult(CancelOutcome.CANCELLED, enrollment, order, paid, choice,
                            tuple(person for _, person in colleagues), course_waitlist, waitlist)

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
