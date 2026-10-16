from datetime import date
from enum import StrEnum

from sqlalchemy import ColumnElement, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from stubbot.db.models import CourseSession, Enrollment, PriceOption, Program, SessionDay, Venue


class SessionScope(StrEnum):
    """Где проведение лежит в админке. Значение едет в кнопках (DictCb.value)."""

    ACTIVE = ""  # раздел «Проведения»: не в архиве и не прошло - то, что стоит в афише
    ARCHIVED = "a"  # «Архив → Проведения»: убрано в архив вручную
    PAST = "p"  # «Архив → Прошедшие мероприятия»: прошло по датам (само, без нажатий)


def session_scope(archived: bool, end_date: date | None, today: date) -> SessionScope:
    """Архив важнее дат: убранное вручную остаётся в «Архив → Проведения», даже когда его даты пройдут."""
    if archived:
        return SessionScope.ARCHIVED
    if end_date is not None and end_date < today:
        return SessionScope.PAST
    return SessionScope.ACTIVE


def _in_scope(scope: SessionScope, today: date) -> list[ColumnElement[bool]]:
    match scope:
        case SessionScope.ARCHIVED:
            return [CourseSession.archived_at.is_not(None)]
        case SessionScope.PAST:
            return [CourseSession.archived_at.is_(None), CourseSession.end_date < today]
    return [CourseSession.archived_at.is_(None),
            or_(CourseSession.end_date.is_(None), CourseSession.end_date >= today)]


class AdminSessionRepository:
    """Проведения (sessions) для админки: все, включая архив и прошедшие."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start_dates(self, scope: SessionScope, today: date) -> list[date | None]:
        """Даты начала проведений раздела (None - без дат) - чтобы показать, в каких месяцах они есть."""
        return list(await self.session.scalars(select(CourseSession.start_date).where(*_in_scope(scope, today))))

    async def count(self, scope: SessionScope, today: date) -> int:
        stmt = select(func.count()).select_from(CourseSession).where(*_in_scope(scope, today))
        return int(await self.session.scalar(stmt) or 0)

    async def list_between(self, scope: SessionScope, today: date, first: date, before: date) -> list[CourseSession]:
        """Проведения раздела, которые начинаются в [first, before), по порядку дат."""
        stmt = (
            select(CourseSession)
            .where(*_in_scope(scope, today), CourseSession.start_date >= first, CourseSession.start_date < before)
            .options(selectinload(CourseSession.program))
            .order_by(CourseSession.start_date, CourseSession.id)
        )
        return list(await self.session.scalars(stmt))

    async def list_without_dates(self, scope: SessionScope, today: date) -> list[CourseSession]:
        """Проведения раздела без дат («даты уточняются») - в порядке создания."""
        stmt = (
            select(CourseSession)
            .where(*_in_scope(scope, today), CourseSession.start_date.is_(None))
            .options(selectinload(CourseSession.program))
            .order_by(CourseSession.id)
        )
        return list(await self.session.scalars(stmt))

    async def get(self, session_id: int) -> CourseSession | None:
        """Со всем, что нужно карточке «как в афише»: курс с лекторами и направлениями, площадка, дни, тарифы."""
        stmt = (
            select(CourseSession)
            .where(CourseSession.id == session_id)
            .options(
                selectinload(CourseSession.program).selectinload(Program.specialties),
                selectinload(CourseSession.program).selectinload(Program.lecturers),
                selectinload(CourseSession.venue),
                selectinload(CourseSession.days).selectinload(SessionDay.venue),
                selectinload(CourseSession.price_options),
            )
        )
        return await self.session.scalar(stmt)

    async def program(self, program_id: int) -> Program | None:
        stmt = (
            select(Program)
            .where(Program.id == program_id)
            .options(selectinload(Program.specialties), selectinload(Program.lecturers))
        )
        return await self.session.scalar(stmt)

    async def programs(self) -> list[Program]:
        return list(await self.session.scalars(select(Program).order_by(Program.sort_order, Program.title)))

    async def venues(self) -> list[Venue]:
        return list(await self.session.scalars(select(Venue).order_by(Venue.name)))

    async def venue(self, venue_id: int) -> Venue | None:
        return await self.session.get(Venue, venue_id)

    async def add(self, course_session: CourseSession) -> CourseSession:
        self.session.add(course_session)
        await self.session.flush()  # нужен id для карточки
        return course_session

    async def active_count_for_program(self, program_id: int, today: date) -> int:
        """Сколько проведений курса сейчас в разделе «Проведения» (то есть в афише)."""
        stmt = select(func.count()).select_from(CourseSession).where(
            CourseSession.program_id == program_id, *_in_scope(SessionScope.ACTIVE, today))
        return int(await self.session.scalar(stmt) or 0)

    async def ids_for_program(self, program_id: int) -> list[int]:
        return list(await self.session.scalars(select(CourseSession.id).where(CourseSession.program_id == program_id)))

    async def has_enrollments(self, session_ids: list[int]) -> bool:
        """Есть ли заявки хоть на одно из проведений - такие удалять нельзя (заявки - данные клиентов)."""
        if not session_ids:
            return False
        stmt = select(Enrollment.id).where(Enrollment.session_id.in_(session_ids)).limit(1)
        return await self.session.scalar(stmt) is not None

    async def delete(self, session_ids: list[int]) -> None:
        """Удалить проведения навсегда вместе с тарифами; дни удаляет база (CASCADE)."""
        if not session_ids:
            return
        await self.session.execute(delete(PriceOption).where(PriceOption.session_id.in_(session_ids)))
        await self.session.execute(delete(CourseSession).where(CourseSession.id.in_(session_ids)))
