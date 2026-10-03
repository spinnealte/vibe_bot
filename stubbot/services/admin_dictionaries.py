"""Общий редактор админки: справочники (лекторы, площадки, специальности, должности) и курсы.

Описание сущности (DictionarySpec) — какие поля, обязательные ли, как проверять ввод. Сервис сущности даёт
одинаковые операции (AdminEntityService), поэтому мастер «➕ Добавить», правка поля и архив — один код на всех.
Записи не удаляются: «в архив» — их не предлагают в новых курсах и при регистрации, у старых данных они остаются.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.models import Lecturer, Position, Specialty, Venue
from stubbot.repositories.admin_dictionaries import AdminDictionaryRepository, DictionaryItem
from stubbot.utils.names import parse_full_name
from stubbot.utils.schedule_input import parse_days, parse_deadline

logger = logging.getLogger(__name__)


class DictKind(StrEnum):
    LECTURERS = "lec"
    VENUES = "ven"
    SPECIALTIES = "spec"
    POSITIONS = "pos"
    COURSES = "crs"
    SESSIONS = "ses"


class FieldKind(StrEnum):
    FULL_NAME = "full_name"  # ФИО — те же правила, что у клиентов
    LINE = "line"  # одна строка, пробелы схлопываем
    HTML = "html"  # текст с форматированием из Telegram (message.html_text)
    URL = "url"  # ссылка http(s)
    NUMBER = "number"  # целое число в [min_value, max_value]
    CHOICE = "choice"  # один вариант из choices — кнопками
    MULTI = "multi"  # несколько записей справочника — кнопками с ✅; значение — список id
    REF = "ref"  # одна запись справочника (курс, площадка) — кнопками; значение — id
    PHOTO = "photo"  # фото сообщением; значение — file_id
    DOCUMENT = "document"  # длинный текст: сообщением с форматированием или файлом .docx / .txt
    DAYS = "days"  # дни проведения: «13.10 10:00-18:00» построчно; значение — список {date, start, end}
    DATETIME = "datetime"  # «11.10 18:00» в часовом поясе центра; значение — ISO-строка
    PRICES = "prices"  # тарифы: категории кнопками, затем подпись и цена каждой; значение — список словарей


# Поля, которые заполняются кнопками, а не текстом.
BUTTON_KINDS = (FieldKind.CHOICE, FieldKind.MULTI, FieldKind.REF)


@dataclass(frozen=True)
class DictField:
    name: str  # колонка или связь модели
    kind: FieldKind
    required: bool
    max_length: int = 0
    min_value: int = 0
    max_value: int = 0
    choices: tuple[str, ...] = ()
    editable: bool = True  # False — только в мастере (у проведения нельзя сменить курс)
    # Спрашивать в мастере, только если другое поле имеет одно из значений: («format», («offline», «hybrid»)).
    when: tuple[str, tuple[str, ...]] | None = None

    def applies(self, values: dict[str, Any]) -> bool:
        return self.when is None or values.get(self.when[0]) in self.when[1]


@dataclass(frozen=True)
class DictionarySpec:
    kind: DictKind
    fields: tuple[DictField, ...]
    model: type | None = None  # для справочников; курсы хранит свой сервис
    unique_title: bool = False  # название уникально (специальности, должности)
    has_sort_order: bool = False

    @property
    def title_field(self) -> str:
        return self.fields[0].name

    def field(self, name: str) -> DictField | None:
        return next((f for f in self.fields if f.name == name), None)


SPECS: dict[DictKind, DictionarySpec] = {
    DictKind.LECTURERS: DictionarySpec(
        kind=DictKind.LECTURERS,
        model=Lecturer,
        fields=(
            DictField("full_name", FieldKind.FULL_NAME, required=True, max_length=300),
            DictField("regalia", FieldKind.LINE, required=False, max_length=300),
            DictField("bio_html", FieldKind.HTML, required=False, max_length=2000),
        ),
    ),
    DictKind.VENUES: DictionarySpec(
        kind=DictKind.VENUES,
        model=Venue,
        fields=(
            DictField("name", FieldKind.LINE, required=True, max_length=128),
            DictField("address", FieldKind.LINE, required=True, max_length=256),
            DictField("map_url", FieldKind.URL, required=False, max_length=512),
            DictField("directions_text", FieldKind.HTML, required=False, max_length=1000),
        ),
    ),
    DictKind.SPECIALTIES: DictionarySpec(
        kind=DictKind.SPECIALTIES,
        model=Specialty,
        fields=(DictField("title", FieldKind.LINE, required=True, max_length=128),),
        unique_title=True,
        has_sort_order=True,
    ),
    DictKind.POSITIONS: DictionarySpec(
        kind=DictKind.POSITIONS,
        model=Position,
        fields=(DictField("title", FieldKind.LINE, required=True, max_length=128),),
        unique_title=True,
        has_sort_order=True,
    ),
}


def parse_field(field: DictField, raw: str, now: datetime | None = None, tz: ZoneInfo | None = None) -> Any:
    """Значение текстового ввода для БД или None — ввод не подходит (пусто, длиннее колонки, не ссылка, не ФИО,
    прошедшая дата). now и tz нужны полям с датами.

    Поля-кнопки, фото и тарифы текстом целиком не заполняются — для них всегда None.
    """
    match field.kind:
        case FieldKind.DAYS:
            return parse_days(raw, now.astimezone(tz).date())
        case FieldKind.DATETIME:
            return parse_deadline(raw, now, tz)
        case FieldKind.FULL_NAME:
            return parse_full_name(raw)
        case FieldKind.LINE:
            value = " ".join(raw.split())
        case FieldKind.HTML | FieldKind.DOCUMENT:
            value = raw.strip()
        case FieldKind.URL:
            value = raw.strip()
            if " " in value or not value.startswith(("https://", "http://")):
                return None
        case FieldKind.NUMBER:
            text = raw.strip()
            if not text.isdigit():
                return None
            number = int(text)
            return number if field.min_value <= number <= field.max_value else None
        case _:
            return None
    return value if 0 < len(value) <= field.max_length else None


class SaveResult(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    DUPLICATE = "duplicate"  # такое название уже есть
    MISSING_REQUIRED = "missing_required"
    BAD_VALUE = "bad_value"  # например, id не из справочника (подделанная кнопка)


class AdminEntityService(Protocol):
    """Что общий редактор умеет делать с любой сущностью админки."""

    spec: DictionarySpec

    async def items(self, parent_id: int | None = None) -> list[Any]: ...  # parent_id — проведения одного курса
    async def item(self, item_id: int) -> Any | None: ...
    def is_active(self, item: Any) -> bool: ...
    def title(self, item: Any) -> str: ...
    def field_value(self, item: Any, field: DictField) -> Any: ...
    async def options(self, field: DictField, selected: list[int]) -> list[tuple[int, str]]: ...
    async def create(self, values: dict[str, Any]) -> tuple[SaveResult, Any | None]: ...
    async def update_field(self, item_id: int, field: DictField, value: Any) -> SaveResult: ...
    async def set_active(self, item_id: int, active: bool) -> Any | None: ...


class AdminDictionaryService:
    def __init__(self, session: AsyncSession, spec: DictionarySpec, admin_client_id: int) -> None:
        self.spec = spec
        self.repo = AdminDictionaryRepository(session, spec.model, spec.title_field)
        self.admin_client_id = admin_client_id

    async def items(self, parent_id: int | None = None) -> list[DictionaryItem]:
        return await self.repo.list_all()

    async def item(self, item_id: int) -> DictionaryItem | None:
        return await self.repo.get(item_id)

    def is_active(self, item: DictionaryItem) -> bool:
        return item.is_active

    def title(self, item: DictionaryItem) -> str:
        return getattr(item, self.spec.title_field)

    def field_value(self, item: DictionaryItem, field: DictField) -> Any:
        return getattr(item, field.name)

    async def options(self, field: DictField, selected: list[int]) -> list[tuple[int, str]]:
        return []  # в справочниках полей-мультивыборов нет

    async def create(self, values: dict[str, Any]) -> tuple[SaveResult, DictionaryItem | None]:
        if any(f.required and not values.get(f.name) for f in self.spec.fields):
            return SaveResult.MISSING_REQUIRED, None
        if self.spec.unique_title and await self.repo.title_taken(values[self.spec.title_field]):
            return SaveResult.DUPLICATE, None
        known = {f.name: values.get(f.name) for f in self.spec.fields}
        if self.spec.has_sort_order:
            known["sort_order"] = await self.repo.next_sort_order()
        item = await self.repo.add(known)
        self._log("создана запись #%s", item.id)
        return SaveResult.OK, item

    async def update_field(self, item_id: int, field: DictField, value: Any) -> SaveResult:
        item = await self.repo.get(item_id)
        if item is None:
            return SaveResult.NOT_FOUND
        if value is None and field.required:
            return SaveResult.MISSING_REQUIRED
        if field.name == self.spec.title_field and self.spec.unique_title and await self.repo.title_taken(
            value, exclude_id=item_id
        ):
            return SaveResult.DUPLICATE
        setattr(item, field.name, value)
        self._log("#%s: %s поле '%s'", item_id, "очищено" if value is None else "изменено", field.name)
        return SaveResult.OK

    async def set_active(self, item_id: int, active: bool) -> DictionaryItem | None:
        item = await self.repo.get(item_id)
        if item is None:
            return None
        item.is_active = active
        self._log("#%s %s", item_id, "возвращена из архива" if active else "в архиве")
        return item

    def _log(self, message: str, *args: object) -> None:
        # Значения полей не пишем: в справочниках ФИО лекторов.
        logger.info("Админ (клиент #%s), %s: " + message, self.admin_client_id, self.spec.kind.value, *args)
