"""Проведения курса (потоки) в админке: мастер «Новое проведение», копирование, правка, статус, скрытие.

Курс — что за обучение (programs); проведение — когда, где и почём (sessions + session_days + price_options).
Новое проведение публикуется сразу (is_visible) и включает курсу показ «без дат» (решение 14).
Тарифы не удаляются: при замене старые выключаются (is_active=false) — на них могут ссылаться будущие заявки.
"""

import logging
from datetime import date, datetime, time
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import DeliveryFormat, PriceKind, PriceUnit, SessionStatus
from stubbot.db.models import CourseSession, PriceOption, SessionDay
from stubbot.repositories.admin_sessions import AdminSessionRepository
from stubbot.services.admin_dictionaries import DictField, DictionarySpec, DictKind, FieldKind, SaveResult
from stubbot.services.schedule import SessionCard
from stubbot.utils.dates import format_range

logger = logging.getLogger(__name__)

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

# Порядок — порядок шагов мастера (решения 02.10.2026).
SESSION_SPEC = DictionarySpec(
    kind=DictKind.SESSIONS,
    fields=(
        DictField("program_id", FieldKind.REF, required=True, editable=False),
        DictField("days", FieldKind.DAYS, required=True),
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

    def __init__(self, session: AsyncSession, admin_client_id: int, today: date) -> None:
        self.repo = AdminSessionRepository(session)
        self.admin_client_id = admin_client_id
        self.today = today

    async def items(self, parent_id: int | None = None) -> list[CourseSession]:
        """Ближайшие и текущие — по дате начала; прошедшие — в конце, свежие первыми."""
        sessions = await self.repo.list_all(parent_id)
        upcoming = [s for s in sessions if s.end_date >= self.today]
        past = sorted((s for s in sessions if s.end_date < self.today), key=lambda s: s.start_date, reverse=True)
        return upcoming + past

    async def item(self, item_id: int) -> CourseSession | None:
        return await self.repo.get(item_id)

    def is_active(self, item: CourseSession) -> bool:
        return item.is_visible

    def is_past(self, item: CourseSession) -> bool:
        return item.end_date < self.today

    def title(self, item: CourseSession) -> str:
        """«13–14 октября · Имплантация…» — дата первой: в списке ищут по ней."""
        return f"{format_range(item.start_date, item.end_date)} · {item.title_override or item.program.title}"

    def field_value(self, item: CourseSession, field: DictField) -> Any:
        match field.name:
            case "days":
                return [{"date": d.date.isoformat(),
                         "start": d.start_time.strftime("%H:%M") if d.start_time else None,
                         "end": d.end_time.strftime("%H:%M") if d.end_time else None} for d in item.days]
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
            return SaveResult.BAD_VALUE, None
        program = await self.repo.program(program_id)
        # Курс — только по id: связь program= через обратную ссылку program.sessions тронула бы незагруженный список.
        item = CourseSession(program_id=program_id, is_visible=True, days=[], price_options=[])
        for field in self.spec.fields:
            if field.name != "program_id" and not await self._apply(item, field, values.get(field.name)):
                return SaveResult.BAD_VALUE, None
        program.show_without_dates = True  # новое проведение — курс снова виден в афише (решение 14)
        await self.repo.add(item)
        self._log("создано и опубликовано проведение #%s курса #%s", item.id, program_id)
        return SaveResult.OK, item

    async def update_field(self, item_id: int, field: DictField, value: Any) -> SaveResult:
        item = await self.repo.get(item_id)
        if item is None:
            return SaveResult.NOT_FOUND
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
        if item is None or status not in ADMIN_STATUSES:
            return None
        item.status = SessionStatus(status)
        self._log("#%s: статус %s", item_id, status)
        return item

    async def set_active(self, item_id: int, active: bool) -> CourseSession | None:
        item = await self.repo.get(item_id)
        if item is None:
            return None
        item.is_visible = active
        self._log("#%s %s", item_id, "показано в афише" if active else "скрыто из афиши")
        return item

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
                if not value:
                    return False
                item.days = [SessionDay(date=date.fromisoformat(d["date"]),
                                        start_time=time.fromisoformat(d["start"]) if d.get("start") else None,
                                        end_time=time.fromisoformat(d["end"]) if d.get("end") else None)
                             for d in value]
                item.start_date, item.end_date = item.days[0].date, item.days[-1].date
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
        logger.info("Админ (клиент #%s), ses: " + message, self.admin_client_id, *args)


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
