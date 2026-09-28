from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.enums import CANCELLED_ENROLLMENT_STATUSES, EnrollmentStatus
from stubbot.db.models import CourseSession, Enrollment, PriceOption, Program, ProgramInterest


class EnrollmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active_for(self, client_id: int, session_id: int) -> Enrollment | None:
        return await self.session.scalar(
            select(Enrollment).where(
                Enrollment.client_id == client_id,
                Enrollment.session_id == session_id,
                Enrollment.status.not_in(CANCELLED_ENROLLMENT_STATUSES),
            )
        )

    async def get_for_client(self, enrollment_id: int, client_id: int) -> Enrollment | None:
        """Только своя заявка: id из callback может быть подделан."""
        return await self.session.scalar(
            select(Enrollment).where(Enrollment.id == enrollment_id, Enrollment.client_id == client_id)
        )

    async def list_for_client(
        self, client_id: int, exclude_statuses: tuple[EnrollmentStatus, ...] = ()
    ) -> list[tuple[Enrollment, CourseSession, Program, PriceOption | None]]:
        """Заявки клиента с потоком, курсом и тарифом; ближайшие потоки — первыми."""
        stmt = (
            select(Enrollment, CourseSession, Program, PriceOption)
            .join(CourseSession, CourseSession.id == Enrollment.session_id)
            .join(Program, Program.id == CourseSession.program_id)
            .outerjoin(PriceOption, PriceOption.id == Enrollment.price_option_id)
            .where(Enrollment.client_id == client_id, Enrollment.status.not_in(exclude_statuses))
            .options(selectinload(CourseSession.venue), selectinload(CourseSession.days))
            .order_by(CourseSession.start_date, Enrollment.id)
        )
        return [tuple(row) for row in (await self.session.execute(stmt)).all()]

    def add(self, enrollment: Enrollment) -> None:
        self.session.add(enrollment)

    async def add_interest(self, client_id: int, program_id: int) -> bool:
        """True — подписка создана сейчас, False — уже была."""
        stmt = (
            pg_insert(ProgramInterest)
            .values(client_id=client_id, program_id=program_id)
            .on_conflict_do_nothing(index_elements=["client_id", "program_id"])
            .returning(ProgramInterest.id)
        )
        return await self.session.scalar(stmt) is not None
