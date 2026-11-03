"""Запросы раздела «👥 Клиенты» админки (полная версия, этап 1, шаг 4.2): поиск, участники проведения, листы
ожидания курсов. Только чтение, кроме отметки «пригласили»."""

from datetime import datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.enums import EnrollmentStatus, OrderStatus
from stubbot.db.models import Client, CourseSession, Enrollment, Order, Program, ProgramInterest

LIKE_ESCAPE = "\\"
ACTIVE_STATUSES = (EnrollmentStatus.APPLICATION, EnrollmentStatus.CONFIRMED, EnrollmentStatus.WAITLIST)


def like_escape(value: str) -> str:
    """% и _ из запроса админа ищем как обычные символы, а не как «что угодно»."""
    return value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", LIKE_ESCAPE + "%").replace("_", LIKE_ESCAPE + "_")


def _folded_name():
    """ФИО без регистра и с «е» вместо «ё»: «петр» найдёт «Пётр»."""
    return func.replace(func.lower(Client.full_name), "ё", "е")


class AdminClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count_clients(self) -> int:
        return int(await self.session.scalar(select(func.count(Client.id)).where(Client.deleted_at.is_(None))) or 0)

    async def search_by_name(self, words: list[str], limit: int) -> list[Client]:
        """Каждое слово запроса (уже в нижнем регистре, ё → е) - часть ФИО."""
        stmt = select(Client).where(Client.deleted_at.is_(None), Client.full_name.is_not(None))
        for word in words:
            stmt = stmt.where(_folded_name().like(f"%{like_escape(word)}%", escape=LIKE_ESCAPE))
        return list(await self.session.scalars(stmt.order_by(Client.full_name, Client.id).limit(limit)))

    async def search_by_phone(self, digits: str, limit: int) -> list[Client]:
        stmt = (
            select(Client)
            .where(Client.deleted_at.is_(None), Client.phone.like(f"%{like_escape(digits)}%", escape=LIKE_ESCAPE))
            .order_by(Client.full_name, Client.id)
            .limit(limit)
        )
        return list(await self.session.scalars(stmt))

    async def page(self, filters: list, order: list, offset: int, limit: int) -> tuple[list[Client], int]:
        """Страница списка клиентов (админка в приложении) и сколько их всего с теми же условиями."""
        where = [Client.deleted_at.is_(None), *filters]
        total = int(await self.session.scalar(select(func.count(Client.id)).where(*where)) or 0)
        stmt = select(Client).where(*where).order_by(*order).offset(offset).limit(limit)
        return list(await self.session.scalars(stmt)), total

    @staticmethod
    def name_filters(words: list[str]) -> list:
        return [Client.full_name.is_not(None)] + [
            _folded_name().like(f"%{like_escape(word)}%", escape=LIKE_ESCAPE) for word in words]

    @staticmethod
    def phone_filter(digits: str):
        return Client.phone.like(f"%{like_escape(digits)}%", escape=LIKE_ESCAPE)

    @staticmethod
    def has_active_enrollment():
        """Есть действующая запись: ждёт оплаты, место закреплено или лист ожидания."""
        return exists().where(Enrollment.client_id == Client.id, Enrollment.status.in_(ACTIVE_STATUSES))

    async def active_enrollment_counts(self, client_ids: list[int]) -> dict[int, int]:
        if not client_ids:
            return {}
        stmt = (
            select(Enrollment.client_id, func.count(Enrollment.id))
            .where(Enrollment.client_id.in_(client_ids), Enrollment.status.in_(ACTIVE_STATUSES))
            .group_by(Enrollment.client_id)
        )
        return {client_id: int(count) for client_id, count in await self.session.execute(stmt)}

    async def client_with_profile(self, client_id: int) -> Client | None:
        stmt = (
            select(Client)
            .where(Client.id == client_id, Client.deleted_at.is_(None))
            .options(selectinload(Client.specialties))
        )
        return await self.session.scalar(stmt)

    async def orders_of(self, client_id: int) -> list[Order]:
        """Действующие заказы, которые клиент оплачивает (ждут оплаты или оплачены)."""
        stmt = (
            select(Order)
            .where(Order.payer_client_id == client_id, Order.status != OrderStatus.CANCELLED)
            .order_by(Order.id)
        )
        return list(await self.session.scalars(stmt))

    async def waiting_courses(self, client_id: int) -> list[tuple[Program, datetime]]:
        """Курсы, нового проведения которых клиент ждёт: с какого времени."""
        stmt = (
            select(Program, ProgramInterest.created_at)
            .join(ProgramInterest, ProgramInterest.program_id == Program.id)
            .where(ProgramInterest.client_id == client_id, ProgramInterest.notified_at.is_(None))
            .order_by(ProgramInterest.created_at)
        )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def session_for_participants(self, session_id: int) -> CourseSession | None:
        stmt = (
            select(CourseSession)
            .where(CourseSession.id == session_id)
            .options(selectinload(CourseSession.program), selectinload(CourseSession.days))
        )
        return await self.session.scalar(stmt)

    async def participants(self, session_id: int) -> list[tuple[Enrollment, Client, Order | None]]:
        """Все записи проведения (и отменённые) с клиентом и заказом - раньше записавшиеся первыми."""
        stmt = (
            select(Enrollment, Client, Order)
            .join(Client, Client.id == Enrollment.client_id)
            .outerjoin(Order, Order.id == Enrollment.order_id)
            .where(Enrollment.session_id == session_id)
            .order_by(Enrollment.created_at, Enrollment.id)
        )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def waitlist_counts(self) -> list[tuple[Program, int]]:
        """Курсы, где кто-то ждёт нового проведения, и сколько человек ждёт."""
        waiting = func.count(ProgramInterest.id)
        stmt = (
            select(Program, waiting)
            .join(ProgramInterest, ProgramInterest.program_id == Program.id)
            .where(ProgramInterest.notified_at.is_(None))
            .group_by(Program.id)
            .order_by(waiting.desc(), Program.title)
        )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def waiting_count(self, program_id: int) -> int:
        stmt = select(func.count(ProgramInterest.id)).where(ProgramInterest.program_id == program_id,
                                                            ProgramInterest.notified_at.is_(None))
        return int(await self.session.scalar(stmt) or 0)

    async def program(self, program_id: int) -> Program | None:
        return await self.session.get(Program, program_id)

    async def course_waitlist(self, program_id: int) -> list[tuple[Client, datetime]]:
        stmt = (
            select(Client, ProgramInterest.created_at)
            .join(ProgramInterest, ProgramInterest.client_id == Client.id)
            .where(ProgramInterest.program_id == program_id, ProgramInterest.notified_at.is_(None),
                   Client.deleted_at.is_(None))
            .order_by(ProgramInterest.created_at)
        )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def mark_invited(self, program_id: int, now: datetime) -> int:
        """Всех ждущих курс - «пригласили»: из листа ожидания они уходят. Сколько человек отметили."""
        stmt = (
            update(ProgramInterest)
            .where(ProgramInterest.program_id == program_id, ProgramInterest.notified_at.is_(None))
            .values(notified_at=now)
        )
        return (await self.session.execute(stmt)).rowcount or 0
