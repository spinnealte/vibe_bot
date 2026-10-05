"""CallbackData-фабрики: данные кнопок парсятся и валидируются aiogram, без строк со split."""

from enum import StrEnum

from aiogram.filters.callback_data import CallbackData

from stubbot.services.admin_dictionaries import DictKind


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
    CLOSE = "close"  # «⬅️ Назад» — закрыть карусель, вернуться в главное меню
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
    VIEW = "view"  # карточка заявки номер index
    ASK_CANCEL = "ask"
    CANCEL = "yes"
    KEEP = "no"  # «Нет, оставить» — назад к той же карточке


class MyApplicationCb(CallbackData, prefix="myapp"):
    action: MyApplicationAction
    enrollment_id: int = 0
    index: int = 0


class EditControl(StrEnum):
    CANCEL = "cancel"
    CLEAR = "clear"


class EditControlCb(CallbackData, prefix="editctl"):
    action: EditControl


# --- Админка ---------------------------------------------------------------------------------------------------

class AdminAction(StrEnum):
    MENU = "menu"
    CLOSE = "close"
    NOOP = "noop"  # счётчик «2/5» между стрелками
    EXPORT = "export"  # выгрузка клиентов в CSV


class AdminCb(CallbackData, prefix="adm"):
    action: AdminAction


class DictAction(StrEnum):
    LIST = "ls"  # список, page — страница
    VIEW = "v"  # карточка записи
    ADD = "add"  # мастер новой записи
    EDIT = "ed"  # изменить поле field
    SKIP = "skip"  # «Пропустить» необязательное поле в мастере
    CLEAR = "clr"  # «Очистить» необязательное поле при правке
    PICK = "pick"  # выбрать вариант value в поле-выборе
    TOGGLE = "tgl"  # отметить/снять запись value в поле-мультивыборе
    DONE = "done"  # «Готово» в мультивыборе
    SAVE = "save"  # сохранить новую запись после предпросмотра
    CANCEL = "cnl"  # отменить мастер или правку
    ARCHIVE = "arc"
    RESTORE = "res"
    PROGRAM = "prog"  # программа курса постранично, page — страница
    STATUS = "st"  # статус проведения в одно нажатие, value — статус
    COPY = "copy"  # «📋 Скопировать» проведение: мастер спросит только новые даты
    SHOW_TBD = "tbd"  # курс без дат в афише: value «1» — показывать, «0» — скрыть


class DictCb(CallbackData, prefix="dic"):
    kind: DictKind
    action: DictAction
    item_id: int = 0
    field: str = ""
    page: int = 0
    value: str = ""
