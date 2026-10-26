"""Клиенты в админке (полная версия, этап 1, шаг 4.2; решения 21.10.2026): поиск по ФИО и телефону, карточка
клиента, участники проведения, листы ожидания курсов, ручная правка аванса.

Поиск: часть ФИО от 2 букв (каждое слово - часть ФИО, без регистра, ё = е) или последние цифры телефона от 4,
не больше SEARCH_LIMIT результатов - выгрузить так всю базу нельзя. В журнал админа пишем только число найденных:
сам запрос - это ФИО или телефон.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import CANCELLED_ENROLLMENT_STATUSES, AdvanceReason, ConsentType, EnrollmentStatus
from stubbot.db.models import Advance, Client, CourseSession, Enrollment, Order, PriceOption, Program
from stubbot.repositories.admin_clients import AdminClientRepository
from stubbot.repositories.enrollments import EnrollmentRepository
from stubbot.repositories.orders import AdvanceRepository
from stubbot.services.admin_log import log_admin_action
from stubbot.services.consents import ConsentService

SEARCH_LIMIT = 100  # пронумерованным списком по 10 на странице - листать можно; больше - уточнить запрос
MIN_NAME_LETTERS = 2
MIN_PHONE_DIGITS = 4
MAX_QUERY = 100
ADVANCE_REASON_LIMIT = 200
LOG_SECTION = "клиенты"

_PHONE_QUERY = re.compile(r"[\d\s+()\-]+")
# Сумма - «5000» или с пробелами между тысячами «5 000»: иначе «-5 000 ошибка» читалось бы как 5 ₽ и «000 ошибка».
_ADVANCE_INPUT = re.compile(r"\s*([+-])\s*(\d{1,3}(?:[  ]\d{3})+|\d+)\s*(?:₽|руб\.?|р\.?)?(?:\s+(.*))?",
                            re.S | re.I)
MAX_ADVANCE_RUB = 10_000_000


class QueryKind(StrEnum):
    NAME = "name"
    PHONE = "phone"
    TOO_SHORT = "too_short"


@dataclass(frozen=True)
class SearchQuery:
    kind: QueryKind
    words: tuple[str, ...] = ()  # части ФИО: нижний регистр, ё → е
    digits: str = ""  # цифры телефона


def parse_query(raw: str) -> SearchQuery:
    """Что ищет админ: телефон (только цифры и + ( ) -, от 4 цифр) или ФИО (от 2 букв)."""
    text = " ".join(raw.split())[:MAX_QUERY]
    if text and _PHONE_QUERY.fullmatch(text):
        digits = re.sub(r"\D", "", text)
        return SearchQuery(QueryKind.PHONE, digits=digits) if len(digits) >= MIN_PHONE_DIGITS \
            else SearchQuery(QueryKind.TOO_SHORT)
    words = tuple(word.lower().replace("ё", "е") for word in text.split())
    if sum(ch.isalpha() for word in words for ch in word) < MIN_NAME_LETTERS:
        return SearchQuery(QueryKind.TOO_SHORT)
    return SearchQuery(QueryKind.NAME, words=words)


class AdvanceInputError(StrEnum):
    INVALID = "invalid"  # не «+5000 причина» / «-5000 причина»
    NO_REASON = "no_reason"
    TOO_LONG = "too_long"


@dataclass(frozen=True)
class AdvanceInput:
    amount: int  # копейки, со знаком
    reason: str


def parse_advance(raw: str) -> AdvanceInput | AdvanceInputError:
    """«+5000 подарок за отзыв» → +500000 коп.; «-5 000 ₽ ошибка» → -500000 коп. Причина обязательна."""
    match = _ADVANCE_INPUT.fullmatch(raw)
    if match is None:
        return AdvanceInputError.INVALID
    sign, digits, reason = match.groups()
    rubles = int(re.sub(r"\s", "", digits))
    if not 0 < rubles <= MAX_ADVANCE_RUB:
        return AdvanceInputError.INVALID
    reason = " ".join((reason or "").split())
    if not reason:
        return AdvanceInputError.NO_REASON
    if len(reason) > ADVANCE_REASON_LIMIT:
        return AdvanceInputError.TOO_LONG
    return AdvanceInput(rubles * 100 * (-1 if sign == "-" else 1), reason)


@dataclass(frozen=True)
class SearchResult:
    query: SearchQuery
    clients: list[Client]
    more: bool = False  # нашлось больше SEARCH_LIMIT - показаны первые


@dataclass(frozen=True)
class AdminClientCard:
    client: Client
    marketing: bool
    advance: int
    # (запись, проведение, курс, тариф, заказ) - как в «Моих записях»; отменённые самим клиентом тоже.
    enrollments: list[tuple[Enrollment, CourseSession, Program, PriceOption | None, Order | None]]
    orders: list[Order]  # действующие заказы, которые он оплачивает
    waiting: list[tuple[Program, datetime]]  # ждёт нового проведения этих курсов


@dataclass(frozen=True)
class SessionParticipants:
    session: CourseSession
    confirmed: list[tuple[Enrollment, Client, Order | None]]
    awaiting: list[tuple[Enrollment, Client, Order | None]]
    waitlist: list[tuple[Enrollment, Client, Order | None]]
    cancelled: list[tuple[Enrollment, Client, Order | None]]

    @property
    def active(self) -> list[tuple[Enrollment, Client, Order | None]]:
        return self.confirmed + self.awaiting + self.waitlist

    @property
    def seats_left(self) -> int | None:
        capacity = self.session.capacity
        return None if capacity is None else max(capacity - len(self.confirmed) - len(self.awaiting), 0)


class AdvanceOutcome(StrEnum):
    DONE = "done"
    NEGATIVE = "negative"  # списание больше аванса
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class AdvanceResult:
    outcome: AdvanceOutcome
    balance: int = 0  # аванс после правки (при отказе - как был)


class AdminClientService:
    def __init__(self, session: AsyncSession, admin_telegram_id: int | None) -> None:
        self.db = session
        self.repo = AdminClientRepository(session)
        self.enrollments = EnrollmentRepository(session)
        self.advances = AdvanceRepository(session)
        self.consents = ConsentService(session)
        self.admin_id = admin_telegram_id

    async def total(self) -> int:
        return await self.repo.count_clients()

    async def search(self, raw: str) -> SearchResult:
        query = parse_query(raw)
        if query.kind is QueryKind.TOO_SHORT:
            return SearchResult(query, [])
        if query.kind is QueryKind.PHONE:
            found = await self.repo.search_by_phone(query.digits, SEARCH_LIMIT + 1)
        else:
            found = await self.repo.search_by_name(list(query.words), SEARCH_LIMIT + 1)
        log_admin_action(self.admin_id, LOG_SECTION, "поиск (%s): найдено %s", query.kind.value, len(found))
        return SearchResult(query, found[:SEARCH_LIMIT], more=len(found) > SEARCH_LIMIT)

    async def card(self, client_id: int) -> AdminClientCard | None:
        client = await self.repo.client_with_profile(client_id)
        if client is None:
            return None
        return AdminClientCard(
            client=client,
            marketing=await self.consents.has_any_consent(client.id, ConsentType.MARKETING),
            advance=await self.advances.balance(client.id),
            enrollments=await self.enrollments.list_for_client(client.id),
            orders=await self.repo.orders_of(client.id),
            waiting=await self.repo.waiting_courses(client.id),
        )

    async def participants(self, session_id: int) -> SessionParticipants | None:
        course_session = await self.repo.session_for_participants(session_id)
        if course_session is None:
            return None
        groups: dict[str, list] = {"confirmed": [], "awaiting": [], "waitlist": [], "cancelled": []}
        for row in await self.repo.participants(session_id):
            status = row[0].status
            if status in CANCELLED_ENROLLMENT_STATUSES:
                groups["cancelled"].append(row)
            elif status is EnrollmentStatus.WAITLIST:
                groups["waitlist"].append(row)
            elif status is EnrollmentStatus.APPLICATION:
                groups["awaiting"].append(row)
            else:
                groups["confirmed"].append(row)  # место закреплено (и прошедшие: был на курсе / не пришёл)
        return SessionParticipants(course_session, **groups)

    async def participants_count(self, session_id: int) -> int:
        """Для кнопки «👥 Участники · N» в карточке проведения: не отменённые записи."""
        rows = await self.repo.participants(session_id)
        return sum(1 for enrollment, _, _ in rows if enrollment.status not in CANCELLED_ENROLLMENT_STATUSES)

    async def waitlists(self) -> list[tuple[Program, int]]:
        return await self.repo.waitlist_counts()

    async def waiting_count(self, program_id: int) -> int:
        return await self.repo.waiting_count(program_id)

    async def course_waitlist(self, program_id: int) -> tuple[Program | None, list[tuple[Client, datetime]]]:
        program = await self.repo.program(program_id)
        if program is None:
            return None, []
        return program, await self.repo.course_waitlist(program_id)

    async def mark_invited(self, program_id: int, now: datetime | None = None) -> int:
        count = await self.repo.mark_invited(program_id, now or datetime.now(UTC))
        log_admin_action(self.admin_id, LOG_SECTION, "курс #%s: лист ожидания - приглашены %s", program_id, count)
        return count

    async def adjust_advance(self, client_id: int, amount: int, reason: str) -> AdvanceResult:
        """Ручная правка аванса (+ начислить, - списать) с причиной. В минус аванс не уходит."""
        if await self.repo.client_with_profile(client_id) is None:
            return AdvanceResult(AdvanceOutcome.NOT_FOUND)
        await self.advances.lock_client(client_id)  # правки и списания одного клиента - по очереди
        balance = await self.advances.balance(client_id)
        if balance + amount < 0:
            return AdvanceResult(AdvanceOutcome.NEGATIVE, balance)
        self.advances.add(Advance(client_id=client_id, amount=amount, reason=AdvanceReason.CORRECTION,
                                  created_by_telegram_id=self.admin_id or 0, comment=reason))
        await self.db.flush()
        log_admin_action(self.admin_id, LOG_SECTION, "клиент #%s: правка аванса %s коп.", client_id, amount)
        return AdvanceResult(AdvanceOutcome.DONE, balance + amount)
