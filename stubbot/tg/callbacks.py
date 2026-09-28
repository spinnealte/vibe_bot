"""CallbackData-фабрики: данные кнопок парсятся и валидируются aiogram, без строк со split."""

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData


class ConsentKind(StrEnum):
    PD = "pd"
    MARKETING = "mkt"


class ConsentCb(CallbackData, prefix="consent"):
    kind: ConsentKind
    accept: bool


class NameAction(StrEnum):
    CONFIRM = "confirm"
    FIX = "fix"


class NameCb(CallbackData, prefix="name"):
    action: NameAction


class SkipOptionalCb(CallbackData, prefix="skip"):
    """«Пропустить» у необязательного поля; field — значение OptionalField."""

    field: str


class SelectGroup(StrEnum):
    SPECIALTIES = "spec"
    POSITIONS = "pos"


class ToggleCb(CallbackData, prefix="tgl"):
    group: SelectGroup
    item_id: int


class SelectDoneCb(CallbackData, prefix="seldone"):
    group: SelectGroup
