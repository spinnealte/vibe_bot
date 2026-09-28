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


class CabinetAction(StrEnum):
    EDIT_MENU = "edit"
    BACK = "back"
    MARKETING = "mkt"
    APPLICATIONS = "apps"


class CabinetCb(CallbackData, prefix="cab"):
    action: CabinetAction


class EditField(StrEnum):
    """Поля, которые можно изменить в кабинете. Необязательные совпадают со значениями OptionalField."""

    FULL_NAME = "name"
    PHONE = "phone"
    EMAIL = "email"
    BIRTH_DATE = "birth_date"
    CITY = "city"
    WORKPLACE = "workplace_raw"
    EXPERIENCE = "practice_since_year"
    SPECIALTIES = "spec"
    POSITIONS = "pos"


class EditFieldCb(CallbackData, prefix="edit"):
    field: EditField


class ScheduleAction(StrEnum):
    """index — позиция курса в карусели (чтобы «Назад» вернул туда же)."""

    SLIDE = "s"  # слайд карусели расписания
    DETAILS = "det"  # полная карточка потока, item_id — поток
    PROGRAM = "prog"  # программа курса постранично, item_id — курс, page — страница
    APPLY = "apply"  # заявка, item_id — поток
    NOTIFY = "notify"  # «Сообщить о наборе», item_id — курс
    NOOP = "noop"  # счётчик «2/5» между стрелками


class ScheduleCb(CallbackData, prefix="sch"):
    action: ScheduleAction
    item_id: int = 0
    index: int = 0
    page: int = 0


class ApplyPriceCb(CallbackData, prefix="aprice"):
    price_id: int


class ApplySeatsCb(CallbackData, prefix="aseats"):
    seats: int


class ApplyAction(StrEnum):
    NO_COMMENT = "nocomment"
    SEND = "send"
    CANCEL = "cancel"


class ApplyCb(CallbackData, prefix="apply"):
    action: ApplyAction


class MyApplicationAction(StrEnum):
    ASK_CANCEL = "ask"
    CANCEL = "yes"
    KEEP = "no"


class MyApplicationCb(CallbackData, prefix="myapp"):
    action: MyApplicationAction
    enrollment_id: int


class EditControl(StrEnum):
    CANCEL = "cancel"
    CLEAR = "clear"


class EditControlCb(CallbackData, prefix="editctl"):
    action: EditControl
