from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.enums import CANCELLED_ENROLLMENT_STATUSES, EnrollmentStatus
from stubbot.db.models import Client, CourseSession, Enrollment, Order, PriceOption, Program, ProgramInterest


class EnrollmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_session(self, session_id: int) -> None:
        """Блокировка строки проведения до конца транзакции: записи на одно проведение идут по очереди, и двое
        не займут одно последнее место одновременно."""
        await self.session.execute(select(CourseSession.id).where(CourseSession.id == session_id).with_for_update())

    async def active_for(self, client_id: int, session_id: int) -> Enrollment | None:
        return await self.session.scalar(
            select(Enrollment).where(
                Enrollment.client_id == client_id,
                Enrollment.session_id == session_id,
                Enrollment.status.not_in(CANCELLED_ENROLLMENT_STATUSES),
            )
        )

    async def get_for_client(self, enrollment_id: int, client_id: int, lock: bool = False) -> Enrollment | None:
        """Только своя запись: id из callback может быть подделан. lock - заблокировать строку до конца транзакции
        и перечитать её (уже загруженный объект получит свежие значения)."""
        stmt = select(Enrollment).where(Enrollment.id == enrollment_id, Enrollment.client_id == client_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return await self.session.scalar(stmt)

    async def get_session(self, session_id: int) -> CourseSession:
        return await self.session.get_one(CourseSession, session_id)

    async def list_for_client(
        self, client_id: int, exclude_statuses: tuple[EnrollmentStatus, ...] = ()
    ) -> list[tuple[Enrollment, CourseSession, Program, PriceOption | None, Order | None]]:
        """Записи клиента с проведением, курсом, тарифом и заказом; ближайшие проведения - первыми."""
        stmt = (
            select(Enrollment, CourseSession, Program, PriceOption, Order)
            .join(CourseSession, CourseSession.id == Enrollment.session_id)
            .join(Program, Program.id == CourseSession.program_id)
            .outerjoin(PriceOption, PriceOption.id == Enrollment.price_option_id)
            .outerjoin(Order, Order.id == Enrollment.order_id)
            .where(Enrollment.client_id == client_id, Enrollment.status.not_in(exclude_statuses))
            .options(selectinload(CourseSession.venue), selectinload(CourseSession.days))
            .order_by(CourseSession.start_date, Enrollment.id)
        )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def colleagues(self, session_id: int, enroller_id: int) -> list[tuple[Enrollment, Client]]:
        """Неотменённые записи коллег, которых этот клиент записал на проведение (у пары - одна), с блокировкой."""
        stmt = (
            select(Enrollment, Client)
            .join(Client, Client.id == Enrollment.client_id)
            .where(
                Enrollment.session_id == session_id,
                Enrollment.enrolled_by_client_id == enroller_id,
                Enrollment.status.not_in(CANCELLED_ENROLLMENT_STATUSES),
            )
            .with_for_update(of=Enrollment)
        )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    async def partners(self, client_id: int, session_ids: list[int]) -> dict[int, Client]:
        """Второй участник пары по проведениям клиента: коллега, которого он записал, или тот, кто записал его."""
        if not session_ids:
            return {}
        colleagues = (
            select(Enrollment.session_id, Client)
            .join(Client, Client.id == Enrollment.client_id)
            .where(
                Enrollment.enrolled_by_client_id == client_id,
                Enrollment.session_id.in_(session_ids),
                Enrollment.status.not_in(CANCELLED_ENROLLMENT_STATUSES),
            )
        )
        enrollers = (
            select(Enrollment.session_id, Client)
            .join(Client, Client.id == Enrollment.enrolled_by_client_id)
            .where(Enrollment.client_id == client_id, Enrollment.session_id.in_(session_ids))
        )
        found: dict[int, Client] = {}
        for stmt in (colleagues, enrollers):
            for session_id, client in (await self.session.execute(stmt)).all():
                found[session_id] = client
        return found

    def add(self, enrollment: Enrollment) -> None:
        self.session.add(enrollment)

    async def put_on_course_waitlist(self, client_id: int, program_id: int) -> None:
        """Лист ожидания курса (та же таблица, что «Сообщить о наборе»): админ пригласит на новое проведение.
        Уже был в списке и его приглашали - снова «ждёт приглашения»."""
        stmt = (
            pg_insert(ProgramInterest)
            .values(client_id=client_id, program_id=program_id)
            .on_conflict_do_update(index_elements=["client_id", "program_id"], set_={"notified_at": None})
        )
        await self.session.execute(stmt)

    async def add_interest(self, client_id: int, program_id: int) -> bool:
        """True - подписка создана сейчас, False - уже была."""
        stmt = (
            pg_insert(ProgramInterest)
            .values(client_id=client_id, program_id=program_id)
            .on_conflict_do_nothing(index_elements=["client_id", "program_id"])
            .returning(ProgramInterest.id)
        )
        return await self.session.scalar(stmt) is not None
