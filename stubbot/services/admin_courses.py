"""Курсы в админке: мастер «Новый курс», правка полей, архив. Тот же общий редактор, что у справочников.

Курс — запись библиотеки: название, программа, лекторы, направления, обложка. Сам по себе курс в афишу не
попадает и из раздела «Курсы» афиша не меняется (решение 16): в афише стоят проведения, с датами или без.
«В архив» — archived_at; курс с проведениями в афише в архив не отправить. Удалить навсегда — только из архива.
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import DeliveryFormat, ProgramLevel
from stubbot.db.models import Lecturer, Program, Specialty
from stubbot.repositories.admin_courses import AdminCourseRepository
from stubbot.repositories.admin_sessions import AdminSessionRepository
from stubbot.services.admin_dictionaries import (
    ENTITY_NAMES,
    DeleteResult,
    DictField,
    DictionarySpec,
    DictKind,
    FieldKind,
    SaveResult,
)
from stubbot.services.admin_log import log_admin_action
from stubbot.utils.names import short_name
from stubbot.utils.slug import slugify

# Порядок — порядок шагов мастера (решение 02.10.2026).
COURSE_SPEC = DictionarySpec(
    kind=DictKind.COURSES,
    fields=(
        DictField("title", FieldKind.LINE, required=True, max_length=256),
        DictField("short_description", FieldKind.LINE, required=False, max_length=200),
        DictField("specialties", FieldKind.MULTI, required=True),
        DictField("lecturers", FieldKind.MULTI, required=True),
        DictField("level", FieldKind.CHOICE, required=False, choices=tuple(level.value for level in ProgramLevel)),
        DictField("default_format", FieldKind.CHOICE, required=True,
                  choices=tuple(fmt.value for fmt in DeliveryFormat)),
        DictField("duration_hours", FieldKind.NUMBER, required=False, min_value=1, max_value=500),
        DictField("program_html", FieldKind.DOCUMENT, required=True, max_length=30000),
        DictField("description_html", FieldKind.HTML, required=False, max_length=2000),
        DictField("cover_file_id", FieldKind.PHOTO, required=False),
    ),
)
_ENUMS = {"level": ProgramLevel, "default_format": DeliveryFormat}


class AdminCourseService:
    spec = COURSE_SPEC

    def __init__(self, session: AsyncSession, admin_telegram_id: int | None, today: date) -> None:
        self.repo = AdminCourseRepository(session)
        self.sessions = AdminSessionRepository(session)  # проведения курса: мешают архиву, удаляются с курсом
        self.admin_telegram_id = admin_telegram_id
        self.today = today

    async def items(self) -> list[Program]:
        """Раздел «Курсы» — только действующие; архивные лежат в «Архиве» (archived_items)."""
        return await self.repo.list_active()

    async def archived_items(self) -> list[Program]:
        return await self.repo.list_archived()

    async def archived_count(self) -> int:
        return await self.repo.archived_count()

    async def item(self, item_id: int) -> Program | None:
        return await self.repo.get(item_id)

    async def siblings(self, item: Program) -> list[int]:
        """Курсы, среди которых листается карточка: действующие или архив — где лежит сам курс."""
        items = await self.items() if self.is_active(item) else await self.archived_items()
        return [course.id for course in items]

    def is_active(self, item: Program) -> bool:
        return item.archived_at is None

    def title(self, item: Program) -> str:
        return item.title

    def field_value(self, item: Program, field: DictField) -> Any:
        value = getattr(item, field.name)
        if field.kind is FieldKind.MULTI:
            return [related.id for related in value]
        if field.kind is FieldKind.CHOICE:
            return value.value if value is not None else None
        return value

    async def options(self, field: DictField, selected: list[int]) -> list[tuple[int, str]]:
        """Действующие записи справочника + уже выбранные (даже если их потом отправили в архив)."""
        if field.name == "specialties":
            items = [s for s in await self.repo.specialties() if s.is_active or s.id in selected]
            return [(s.id, s.title) for s in items]
        if field.name == "lecturers":
            items = [lec for lec in await self.repo.lecturers() if lec.is_active or lec.id in selected]
            return [(lec.id, short_name(lec.full_name)) for lec in items]
        return []

    async def create(self, values: dict[str, Any]) -> tuple[SaveResult, Program | None]:
        if any(f.required and not values.get(f.name) for f in self.spec.fields):
            return SaveResult.MISSING_REQUIRED, None
        program = Program(slug=await self._free_slug(values["title"]), is_published=True)
        for field in self.spec.fields:
            if not await self._apply(program, field, values.get(field.name), current=[]):
                return SaveResult.BAD_VALUE, None
        await self.repo.add(program)
        self._log("создан курс #%s", program.id)
        return SaveResult.OK, program

    async def update_field(self, item_id: int, field: DictField, value: Any) -> SaveResult:
        program = await self.repo.get(item_id)
        if program is None or program.archived_at is not None:
            return SaveResult.NOT_FOUND  # в архиве курс не правится: карточка там — «как было»
        if field.required and not value:
            return SaveResult.MISSING_REQUIRED
        if not await self._apply(program, field, value, current=self.field_value(program, field)):
            return SaveResult.BAD_VALUE
        self._log("#%s: %s поле '%s'", item_id, "очищено" if not value else "изменено", field.name)
        return SaveResult.OK

    async def sessions_in_afisha(self, item_id: int) -> int:
        """Сколько проведений курса сейчас в афише. Пока они есть, курс в архив не отправить: из раздела
        «Курсы» афиша не меняется — сначала в архив уходят сами проведения."""
        return await self.sessions.active_count_for_program(item_id, self.today)

    async def set_active(self, item_id: int, active: bool) -> Program | None:
        """В архив / из архива. None — курса нет или в архив нельзя: у него есть проведения в афише."""
        program = await self.repo.get(item_id)
        if program is None or (not active and await self.sessions_in_afisha(item_id)):
            return None
        program.archived_at = None if active else datetime.now(UTC)
        self._log("#%s %s", item_id, "возвращён из архива" if active else "в архиве")
        return program

    async def sessions_total(self, item_id: int) -> int:
        """Сколько у курса проведений всего (в архиве и прошедших) — они удаляются вместе с курсом."""
        return len(await self.sessions.ids_for_program(item_id))

    async def can_delete(self, item_id: int) -> DeleteResult:
        program = await self.repo.get(item_id)
        if program is None:
            return DeleteResult.NOT_FOUND
        if program.archived_at is None:
            return DeleteResult.NOT_IN_ARCHIVE
        if await self.sessions_in_afisha(item_id):
            return DeleteResult.HAS_ACTIVE_SESSIONS
        session_ids = await self.sessions.ids_for_program(item_id)
        if await self.sessions.has_enrollments(session_ids) or await self.repo.has_interests(item_id):
            return DeleteResult.IN_USE
        return DeleteResult.OK

    async def delete(self, item_id: int) -> DeleteResult:
        """Удалить курс навсегда — только из архива, вместе с его проведениями (они все в архиве или прошли)."""
        result = await self.can_delete(item_id)
        if result is not DeleteResult.OK:
            return result
        session_ids = await self.sessions.ids_for_program(item_id)
        await self.sessions.delete(session_ids)
        await self.repo.delete(item_id)
        self._log("#%s удалён навсегда, вместе с ним проведений: %s", item_id, len(session_ids))
        return DeleteResult.OK

    async def preview(self, values: dict[str, Any]) -> Program:
        """Несохранённый курс для предпросмотра карточки — в сессию не попадает."""
        program = Program()
        for field in self.spec.fields:
            await self._apply(program, field, values.get(field.name), current=[])
        return program

    async def _apply(self, program: Program, field: DictField, value: Any, current: Any) -> bool:
        """Записать значение поля в курс. False — значение не подходит (подделанная кнопка, не тот вариант)."""
        match field.kind:
            case FieldKind.MULTI:
                ids = sorted(set(value or []))
                allowed = {item_id for item_id, _ in await self.options(field, list(current or []))}
                if not set(ids) <= allowed:
                    return False
                loader = self.repo.specialties if field.name == "specialties" else self.repo.lecturers
                related: list[Specialty] | list[Lecturer] = await loader(ids) if ids else []
                setattr(program, field.name, related)
            case FieldKind.CHOICE:
                if value is not None and value not in field.choices:
                    return False
                setattr(program, field.name, _ENUMS[field.name](value) if value is not None else None)
            case _:
                setattr(program, field.name, value)
        return True

    async def _free_slug(self, title: str) -> str:
        base = slugify(title)
        slug, n = base, 1
        while await self.repo.slug_taken(slug):
            n += 1
            slug = f"{base}-{n}"
        return slug

    def _log(self, message: str, *args: object) -> None:
        log_admin_action(self.admin_telegram_id, ENTITY_NAMES[DictKind.COURSES], message, *args)
