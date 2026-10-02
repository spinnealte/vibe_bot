"""Справочники для админки: лекторы, площадки, специальности, должности — один общий редактор.

Описание каждого справочника (DictionarySpec) — какие поля, обязательные ли, как проверять ввод. Записи не удаляются:
«в архив» (is_active=false) — их не предлагают в новых курсах и при регистрации, но у старых данных они остаются.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.models import Lecturer, Position, Specialty, Venue
from stubbot.repositories.admin_dictionaries import AdminDictionaryRepository, DictionaryItem
from stubbot.utils.names import parse_full_name

logger = logging.getLogger(__name__)


class DictKind(StrEnum):
    LECTURERS = "lec"
    VENUES = "ven"
    SPECIALTIES = "spec"
    POSITIONS = "pos"


class FieldKind(StrEnum):
    FULL_NAME = "full_name"  # ФИО — те же правила, что у клиентов
    LINE = "line"  # одна строка, пробелы схлопываем
    HTML = "html"  # текст с форматированием из Telegram (message.html_text)
    URL = "url"  # ссылка http(s)


@dataclass(frozen=True)
class DictField:
    name: str  # колонка модели
    kind: FieldKind
    required: bool
    max_length: int


@dataclass(frozen=True)
class DictionarySpec:
    kind: DictKind
    model: type[DictionaryItem]
    fields: tuple[DictField, ...]
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


def parse_field(field: DictField, raw: str) -> str | None:
    """Значение для БД или None — ввод не подходит (пусто, длиннее колонки, не ссылка, не ФИО)."""
    match field.kind:
        case FieldKind.FULL_NAME:
            return parse_full_name(raw)
        case FieldKind.LINE:
            value = " ".join(raw.split())
        case FieldKind.HTML:
            value = raw.strip()
        case FieldKind.URL:
            value = raw.strip()
            if " " in value or not value.startswith(("https://", "http://")):
                return None
    return value if 0 < len(value) <= field.max_length else None


class SaveResult(StrEnum):
    OK = "ok"
    NOT_FOUND = "not_found"
    DUPLICATE = "duplicate"  # такое название уже есть
    MISSING_REQUIRED = "missing_required"


class AdminDictionaryService:
    def __init__(self, session: AsyncSession, spec: DictionarySpec, admin_client_id: int) -> None:
        self.spec = spec
        self.repo = AdminDictionaryRepository(session, spec.model, spec.title_field)
        self.admin_client_id = admin_client_id

    async def items(self) -> list[DictionaryItem]:
        return await self.repo.list_all()

    async def item(self, item_id: int) -> DictionaryItem | None:
        return await self.repo.get(item_id)

    async def create(self, values: dict[str, str | None]) -> tuple[SaveResult, DictionaryItem | None]:
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

    async def update_field(self, item_id: int, field: DictField, value: str | None) -> SaveResult:
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
