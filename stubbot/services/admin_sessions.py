"""Проведения курса (потоки) в админке: мастер «Новое проведение», копирование, правка, статус, архив, удаление.

Курс — что за обучение (programs); проведение — когда, где и почём (sessions + session_days + price_options).
Афиша состоит только из проведений (решение 16): новое проведение сразу в афише, с датами или без дат
(«даты уточняются»). Убрать из афиши — отправить в архив; прошедшее по датам само уходит в «Прошедшие
мероприятия». Удалить навсегда можно только из архива и из прошедших.
Тарифы при замене не удаляются, а выключаются (is_active=false) — на них могут ссылаться будущие заявки.
"""

from collections import Counter
from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import DeliveryFormat, PriceKind, PriceUnit, SessionStatus
from stubbot.db.models import CourseSession, PriceOption, SessionDay
from stubbot.repositories.admin_sessions import AdminSessionRepository, SessionScope, session_scope
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
from stubbot.services.schedule import SessionCard
from stubbot.utils.dates import format_range, month_bounds, month_key

# Категории тарифов — как в пет-проекте. «Для двух коллег» — цена за группу из двух человек.
PRICE_KINDS = (PriceKind.FULL.value, PriceKind.GROUP.value, PriceKind.THEORY.value, PriceKind.PRACTICE.value)
MAX_PRICE_LABEL = 128
# Статусы, которые админ ставит вручную (кнопками). Остальные — для будущих заявок.
ADMIN_STATUSES = (
    SessionStatus.ANNOUNCED.value,
    SessionStatus.REGISTRATION_OPEN.value,
    SessionStatus.FULL.value,
    SessionStatus.REGISTRATION_CLOSED.value,
)
_FORMATS = tuple(fmt.value for fmt in DeliveryFormat)
_WITH_VENUE = (DeliveryFormat.OFFLINE.value, DeliveryFormat.HYBRID.value)
_WITH_LINK = (DeliveryFormat.ONLINE.value, DeliveryFormat.HYBRID.value)
# «Месяц» проведений без дат в кнопках и листании (настоящие месяцы — ГГГГММ).
NO_DATES = 0

# Порядок — порядок шагов мастера (решения 02.10.2026).
SESSION_SPEC = DictionarySpec(
    kind=DictKind.SESSIONS,
    fields=(
        DictField("program_id", FieldKind.COURSE, required=True, editable=False),
        # Необязательно: без дней проведение стоит в афише с пометкой «даты уточняются».
        DictField("days", FieldKind.DAYS, required=False),
        DictField("format", FieldKind.CHOICE, required=True, choices=_FORMATS),
        DictField("venue_id", FieldKind.REF, required=False, when=("format", _WITH_VENUE)),
        DictField("online_url", FieldKind.URL, required=False, max_length=512, when=("format", _WITH_LINK)),
        DictField("capacity", FieldKind.NUMBER, required=False, min_value=1, max_value=1000),
        DictField("prices", FieldKind.PRICES, required=False),
        DictField("registration_deadline", FieldKind.DATETIME, required=False),
        # Статус меняется кнопками прямо в карточке проведения — отдельной правки «✏️» у него нет.
        DictField("status", FieldKind.CHOICE, required=True, choices=ADMIN_STATUSES, editable=False),
        DictField("title_override", FieldKind.LINE, required=False, max_length=256),
        DictField("cover_file_id", FieldKind.PHOTO, required=False),
    ),
)
# В копии спрашиваем только новые даты, остальное — из исходного проведения.
COPY_FIELDS = ("days",)


def valid_prices(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, list) or len(value) > len(PRICE_KINDS):
        return False
    for price in value:
        if (not isinstance(price, dict) or price.get("kind") not in PRICE_KINDS
                or not 0 < len(str(price.get("label", ""))) <= MAX_PRICE_LABEL
                or not isinstance(price.get("amount"), int) or price["amount"] <= 0):
            return False
    return True


class AdminSessionService:
    spec = SESSION_SPEC

    def __init__(self, session: AsyncSession, admin_telegram_id: int | None, today: date) -> None:
        self.repo = AdminSessionRepository(session)
        self.admin_telegram_id = admin_telegram_id
        self.today = today

    async def items(self) -> list[CourseSession]:
        """Не используется: проведения показываются не списком, а по месяцам — months() и month_items()."""
        return []

    def scope(self, item: CourseSession) -> SessionScope:
        """Где проведение лежит: в афише, в архиве (убрано вручную) или в прошедших."""
        return session_scope(item.archived_at is not None, item.end_date, self.today)

    async def count(self, scope: SessionScope) -> int:
        return await self.repo.count(scope, self.today)

    async def months(self, scope: SessionScope = SessionScope.ACTIVE) -> list[tuple[int, int]]:
        """Месяцы раздела, в которых есть проведения (по дате начала): [(ГГГГММ, сколько)] по порядку;
        проведения без дат — под ключом NO_DATES, в конце.

        В админке проведения не показываются одним длинным списком: сначала месяц, внутри — листание стрелками.
        """
        counts = Counter(month_key(day) if day else NO_DATES for day in await self.repo.start_dates(scope, self.today))
        without_dates = counts.pop(NO_DATES, 0)
        return sorted(counts.items()) + ([(NO_DATES, without_dates)] if without_dates else [])

    async def month_items(self, month: int, scope: SessionScope = SessionScope.ACTIVE) -> list[CourseSession]:
        """Проведения раздела за месяц ГГГГММ по порядку дат (NO_DATES — без дат) — их и листают стрелками."""
        if month == NO_DATES:
            return await self.repo.list_without_dates(scope, self.today)
        bounds = month_bounds(month)
        return await self.repo.list_between(scope, self.today, *bounds) if bounds else []

    def month_of(self, item: CourseSession) -> int:
        return month_key(item.start_date) if item.start_date else NO_DATES

    async def siblings(self, item: CourseSession) -> list[int]:
        """Проведения, среди которых листается карточка: тот же раздел и тот же месяц (или все без дат)."""
        return [s.id for s in await self.month_items(self.month_of(item), self.scope(item))]

    async def item(self, item_id: int) -> CourseSession | None:
        return await self.repo.get(item_id)

    def is_active(self, item: CourseSession) -> bool:
        return item.archived_at is None

    def is_past(self, item: CourseSession) -> bool:
        return item.end_date is not None and item.end_date < self.today

    def title(self, item: CourseSession) -> str:
        """«13–14 октября · Имплантация…» — дата первой: в списке ищут по ней."""
        dates = format_range(item.start_date, item.end_date) if item.start_date else "без дат"
        return f"{dates} · {item.title_override or item.program.title}"

    def field_value(self, item: CourseSession, field: DictField) -> Any:
        match field.name:
            case "days":
                return [{"date": d.date.isoformat(),
                         "start": d.start_time.strftime("%H:%M") if d.start_time else None,
                         "end": d.end_time.strftime("%H:%M") if d.end_time else None} for d in item.days] or None
            case "prices":
                return [{"kind": p.kind.value, "label": p.label, "amount": p.amount}
                        for p in item.price_options if p.is_active] or None
            case "registration_deadline":
                return item.registration_deadline.isoformat() if item.registration_deadline else None
            case "format" | "status":
                return getattr(item, field.name).value
        return getattr(item, field.name)

    async def options(self, field: DictField, selected: list[int]) -> list[tuple[int, str]]:
        """Курсы не из архива и действующие площадки + уже выбранная запись (даже если она в архиве)."""
        if field.name == "program_id":
            return [(p.id, p.title) for p in await self.repo.programs()
                    if p.archived_at is None or p.id in selected]
        if field.name == "venue_id":
            return [(v.id, v.name) for v in await self.repo.venues() if v.is_active or v.id in selected]
        return []

    async def copy_values(self, item_id: int) -> dict[str, Any] | None:
        """Значения проведения для «📋 Скопировать»: всё, кроме дней (их спросим заново).

        Срок «запись до» и статус относятся к старым датам: срок сбрасываем, статус — «идёт набор»
        (поменять можно одной кнопкой в карточке копии).
        """
        item = await self.repo.get(item_id)
        if item is None:
            return None
        values = {f.name: self.field_value(item, f) for f in self.spec.fields if f.name not in COPY_FIELDS}
        values["registration_deadline"] = None
        values["status"] = SessionStatus.REGISTRATION_OPEN.value
        return values

    async def create(self, values: dict[str, Any]) -> tuple[SaveResult, CourseSession | None]:
        if any(f.required and f.applies(values) and not values.get(f.name) for f in self.spec.fields):
            return SaveResult.MISSING_REQUIRED, None
        program_id = values["program_id"]
        if program_id not in {pid for pid, _ in await self.options(self.spec.field("program_id"), [])}:
            return SaveResult.BAD_VALUE, None  # курса нет или он в архиве
        # Курс — только по id: связь program= через обратную ссылку program.sessions тронула бы незагруженный список.
        item = CourseSession(program_id=program_id, days=[], price_options=[])
        for field in self.spec.fields:
            if field.name != "program_id" and not await self._apply(item, field, values.get(field.name)):
                return SaveResult.BAD_VALUE, None
        await self.repo.add(item)
        self._log("создано проведение #%s курса #%s (%s) — в афише", item.id, program_id,
                  "с датами" if item.start_date else "без дат")
        return SaveResult.OK, item

    async def update_field(self, item_id: int, field: DictField, value: Any) -> SaveResult:
        item = await self.repo.get(item_id)
        if item is None or self.scope(item) is not SessionScope.ACTIVE:
            return SaveResult.NOT_FOUND  # в архиве и в прошедших проведение не правится: карточка там — «как было»
        if not field.editable:
            return SaveResult.BAD_VALUE
        if field.required and not value:
            return SaveResult.MISSING_REQUIRED
        if not await self._apply(item, field, value):
            return SaveResult.BAD_VALUE
        self._log("#%s: %s поле '%s'", item_id, "очищено" if not value else "изменено", field.name)
        return SaveResult.OK

    async def set_status(self, item_id: int, status: str) -> CourseSession | None:
        item = await self.repo.get(item_id)
        if item is None or status not in ADMIN_STATUSES or self.scope(item) is not SessionScope.ACTIVE:
            return None
        item.status = SessionStatus(status)
        self._log("#%s: статус %s", item_id, status)
        return item

    async def set_active(self, item_id: int, active: bool) -> CourseSession | None:
        """В архив (убрать из афиши) / из архива. None — проведения нет или вернуть нельзя: его курс в архиве."""
        item = await self.repo.get(item_id)
        if item is None or (active and item.program.archived_at is not None):
            return None
        item.archived_at = None if active else datetime.now(UTC)
        self._log("#%s %s", item_id, "возвращено из архива" if active else "в архиве (убрано из афиши)")
        return item

    async def can_delete(self, item_id: int) -> DeleteResult:
        item = await self.repo.get(item_id)
        if item is None:
            return DeleteResult.NOT_FOUND
        if self.scope(item) is SessionScope.ACTIVE:
            return DeleteResult.NOT_IN_ARCHIVE
        if await self.repo.has_enrollments([item_id]):
            return DeleteResult.IN_USE
        return DeleteResult.OK

    async def delete(self, item_id: int) -> DeleteResult:
        """Удалить проведение навсегда (с днями и тарифами) — только из архива или из прошедших."""
        result = await self.can_delete(item_id)
        if result is DeleteResult.OK:
            await self.repo.delete([item_id])
            self._log("#%s удалено навсегда", item_id)
        return result

    async def preview(self, values: dict[str, Any]) -> SessionCard:
        """Проведение для предпросмотра «как в афише». Простой объект, а не ORM: он не должен попасть в БД
        (ORM-объект с program= через обратную связь program.sessions мог бы сохраниться при следующем flush)."""
        item = SimpleNamespace(
            program=await self.repo.program(values["program_id"]), title_override=None, venue=None, venue_id=None,
            days=[], price_options=[], start_date=None, end_date=None, format=None, status=None,
            registration_deadline=None, online_url=None, capacity=None, cover_file_id=None,
        )
        for field in self.spec.fields:
            if field.name != "program_id":
                await self._apply(item, field, values.get(field.name))
        return SessionCard(session=item, seats_left=None)

    async def _apply(self, item: Any, field: DictField, value: Any) -> bool:
        """Записать значение поля в проведение. False — значение не подходит (подделанная кнопка и т. п.)."""
        match field.name:
            case "days":
                # Пусто — проведение без дат: «даты уточняются».
                item.days = [SessionDay(date=date.fromisoformat(d["date"]),
                                        start_time=time.fromisoformat(d["start"]) if d.get("start") else None,
                                        end_time=time.fromisoformat(d["end"]) if d.get("end") else None)
                             for d in value or []]
                item.start_date = item.days[0].date if item.days else None
                item.end_date = item.days[-1].date if item.days else None
            case "format":
                if value not in _FORMATS:
                    return False
                item.format = DeliveryFormat(value)
            case "status":
                if value not in ADMIN_STATUSES:
                    return False
                item.status = SessionStatus(value)
            case "venue_id":
                if value is not None and value not in {vid for vid, _ in await self.options(field, [item.venue_id])}:
                    return False
                item.venue = await self.repo.venue(value) if value is not None else None
            case "prices":
                if not valid_prices(value):
                    return False
                for old in item.price_options:
                    old.is_active = False  # не удаляем: на тариф могут ссылаться заявки
                item.price_options.extend(_price_option(i, price) for i, price in enumerate(value or []))
            case "registration_deadline":
                item.registration_deadline = datetime.fromisoformat(value) if value else None
            case _:
                setattr(item, field.name, value)
        return True

    def _log(self, message: str, *args: object) -> None:
        log_admin_action(self.admin_telegram_id, ENTITY_NAMES[DictKind.SESSIONS], message, *args)


def _price_option(index: int, price: dict[str, Any]) -> PriceOption:
    group = price["kind"] == PriceKind.GROUP.value
    return PriceOption(
        kind=PriceKind(price["kind"]),
        label=price["label"],
        amount=price["amount"],
        unit=PriceUnit.PER_GROUP if group else PriceUnit.PER_PERSON,
        min_seats=2 if group else 1,
        max_seats=2 if group else None,
        is_active=True,
        sort_order=(index + 1) * 10,
    )
