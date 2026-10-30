"""CallbackData-фабрики: данные кнопок парсятся и валидируются aiogram, без строк со split.

Числа в кнопках - в пределах bigint PostgreSQL (DbInt): данные кнопки присылает клиент Telegram, их можно подделать,
и слишком большое число уронило бы запрос к базе. Вне пределов - кнопка не подходит ни одному хендлеру, человеку
«Эта кнопка уже неактуальна» (ошибка найдена 28.10.2026).
"""

from enum import StrEnum
from typing import Annotated

from aiogram.filters.callback_data import CallbackData
from pydantic import Field

from stubbot.services.admin_dictionaries import DictKind

DbInt = Annotated[int, Field(ge=-(2**63), le=2**63 - 1)]


class ConsentKind(StrEnum):
    PD = "pd"
    MARKETING = "mkt"


class ConsentCb(CallbackData, prefix="consent"):
    """«Да / Нет» на включение рекламных рассылок из кабинета (kind - MARKETING). При регистрации согласия
    даются галочками (ConsentFormCb)."""

    kind: ConsentKind
    accept: bool


class ConsentFormAction(StrEnum):
    PD = "pd"  # галочка 1 - политика и обработка ПД (обязательна)
    ADS = "ads"  # галочка 2 - рекламные рассылки (по желанию)
    CONTINUE = "go"  # «Продолжить» - пускает дальше только с первой галочкой


class ConsentFormCb(CallbackData, prefix="cform"):
    action: ConsentFormAction


class NameAction(StrEnum):
    CONFIRM = "confirm"
    FIX = "fix"


class NameCb(CallbackData, prefix="name"):
    action: NameAction


class SkipOptionalCb(CallbackData, prefix="skip"):
    """«Пропустить» у необязательного поля; field - значение OptionalField."""

    field: str


class SelectGroup(StrEnum):
    SPECIALTIES = "spec"
    POSITIONS = "pos"


class ToggleCb(CallbackData, prefix="tgl"):
    group: SelectGroup
    item_id: DbInt


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
    """index - позиция курса в карусели (чтобы «Назад» вернул туда же)."""

    SLIDE = "s"  # слайд карусели расписания
    CLOSE = "close"  # «⬅️ Назад» - закрыть карусель, вернуться в главное меню
    PROGRAM = "prog"  # программа курса постранично, item_id - курс, page - страница
    APPLY = "apply"  # заявка, item_id - поток
    NOTIFY = "notify"  # «Сообщить о наборе», item_id - курс
    NOOP = "noop"  # счётчик «2/5» между стрелками


class ScheduleCb(CallbackData, prefix="sch"):
    action: ScheduleAction
    item_id: DbInt = 0
    index: DbInt = 0
    page: DbInt = 0


class ApplyPriceCb(CallbackData, prefix="aprice"):
    price_id: DbInt


class ApplyAction(StrEnum):
    COLLEAGUE_OK = "colleague"  # галочка «Коллега согласен на передачу его ФИО и телефона»
    SEND = "send"  # «✅ Записаться»
    CANCEL = "cancel"


class ApplyCb(CallbackData, prefix="apply"):
    action: ApplyAction


class OrderAction(StrEnum):
    INDIVIDUAL = "ind"  # оплачиваю как физическое лицо
    ORGANIZATION = "org"  # как юридическое лицо - пришлю реквизиты файлом


class OrderCb(CallbackData, prefix="order"):
    """Кнопки под «Вы записаны»: заказ оплачивает только его плательщик (id из кнопки сверяется)."""

    action: OrderAction
    order_id: DbInt


class PayAction(StrEnum):
    """Онлайн-оплата (шаг 7). item_id - заказ (START) или наш платёж (остальные); index - карточка «Моих записей»,
    куда вернёт «К моим записям»."""

    START = "go"  # «💳 Оплатить онлайн» - ссылка на весь остаток
    CHECK = "chk"  # «🔄 Проверить оплату»
    FAKE_OK = "fok"  # тестовая оплата (ONLINE_PAYMENTS=fake): «🧪 Оплата прошла»
    FAKE_FAIL = "ffl"  # «🧪 Оплата не прошла»


class PayCb(CallbackData, prefix="pay"):
    action: PayAction
    item_id: DbInt
    index: DbInt = 0


class MyApplicationAction(StrEnum):
    VIEW = "view"  # карточка записи номер index
    PAY = "pay"  # «Как будете оплачивать?» - выбор «Физлицо / Юрлицо» для заказа этой записи
    ASK_CANCEL = "ask"
    CANCEL = "yes"  # «Да, отменить» - оплаты не было (или выйти из листа ожидания)
    # Оплата была: выбор, что сделать с деньгами, он же подтверждение отмены.
    TRANSFER = "tr"
    ADVANCE = "adv"
    REFUND = "ref"
    KEEP = "no"  # «Нет, оставить» - назад к той же карточке


class MyApplicationCb(CallbackData, prefix="myapp"):
    action: MyApplicationAction
    enrollment_id: DbInt = 0
    index: DbInt = 0


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
    ARCHIVE = "arch"  # раздел «Архив»: курсы, проведения, прошедшие мероприятия


class AdminCb(CallbackData, prefix="adm"):
    action: AdminAction


class OrderAdminAction(StrEnum):
    """Раздел «🧾 Заказы» (полная версия, этап 1, шаг 4). order_id - заказ; value - раздел или способ оплаты."""

    MENU = "menu"  # разделы с числом заказов
    LIST = "ls"  # заказы раздела value, страница page
    VIEW = "v"  # карточка заказа
    FIND = "find"  # поиск по номеру
    PAY = "pay"  # отметить оплату: спросить сумму
    FULL = "full"  # «Полностью» - весь остаток
    METHOD = "m"  # способ оплаты value - он же подтверждение: оплата сохраняется
    TOTAL = "total"  # указать сумму заказа (её не знали при записи)
    INVOICE = "inv"  # отправить счёт плательщику
    INVOICE_MANUAL = "invm"  # «✔️ Счёт отправлен вне бота» - отметка
    INVOICE_UNDO = "invu"  # «↩️ Счёт ещё не отправлен» - снять ручную отметку
    WRITE = "write"  # написать плательщику
    SEND = "send"  # «✅ Отправить» после предпросмотра
    REQUISITES = "req"  # прислать админу файл реквизитов
    REMOVE = "rm"  # снять бронь: вопрос
    REMOVE_OK = "rmok"
    CREDIT = "cr"  # отменённый заказ: оплату - на аванс
    CREDIT_OK = "crok"
    REFUND = "rf"  # отменённый заказ: деньги вернули
    REFUND_OK = "rfok"
    APPLY = "ap"  # зачесть аванс в заказ
    APPLY_OK = "apok"
    CANCEL = "cnl"  # бросить ввод - назад к карточке
    NOOP = "noop"  # счётчик страниц


class OrderAdminCb(CallbackData, prefix="aord"):
    action: OrderAdminAction
    order_id: DbInt = 0
    value: str = ""
    page: DbInt = 0


class ClientAdminAction(StrEnum):
    """Раздел «👥 Клиенты» (полная версия, этап 1, шаг 4.2). item_id - клиент, проведение или курс (см. действие);
    value у карточки клиента - откуда пришли (туда ведёт «⬅️ Назад»), у «Отправить» и правки аванса - ключ."""

    MENU = "menu"
    FIND = "find"  # поиск по ФИО или телефону
    VIEW = "v"  # карточка клиента item_id
    WRITE = "write"  # написать клиенту item_id
    SEND = "send"  # «✅ Отправить» после предпросмотра
    ADVANCE = "adv"  # правка аванса клиента item_id
    ADVANCE_OK = "advok"
    PARTICIPANTS = "part"  # участники проведения item_id
    CANCELLED = "cnld"  # отменившие запись на проведение item_id
    WAITLISTS = "wls"  # курсы, где кто-то ждёт нового проведения
    WAITLIST = "wl"  # лист ожидания курса item_id
    INVITED = "inv"  # «✔️ Пригласили всех»: вопрос
    INVITED_OK = "invok"
    CANCEL = "cnl"  # бросить ввод - назад к карточке клиента


class ClientAdminCb(CallbackData, prefix="acl"):
    action: ClientAdminAction
    item_id: DbInt = 0
    value: str = ""


class PickCb(CallbackData, prefix="pick"):
    """◀️ ▶️ под пронумерованным списком админки (tg/picker.py). token - какой это список: стрелки под старым
    списком (уже открыт другой) не листают новый."""

    token: str
    page: DbInt = -1  # -1 - счётчик «2/5» между стрелками


class DictAction(StrEnum):
    # Список, page - страница. У проведений - месяцы раздела: value - SessionScope («» афиша, «a» архив,
    # «p» прошедшие). У курсов value «a» - курсы из архива.
    LIST = "ls"
    MONTH = "mon"  # проведения месяца: page - месяц ГГГГММ (0 - без дат), value - раздел; дальше листание
    VIEW = "v"  # карточка записи; курс и проведение сами знают, в каком они разделе
    ADD = "add"  # мастер новой записи
    EDIT = "ed"  # изменить поле field
    SKIP = "skip"  # «Пропустить» необязательное поле в мастере
    CLEAR = "clr"  # «Очистить» необязательное поле при правке
    PICK = "pick"  # выбрать вариант value в поле-выборе
    BROWSE = "br"  # ◀️ ▶️ на шаге «Курс» мастера проведения: показать карточку курса value
    TOGGLE = "tgl"  # отметить/снять запись value в поле-мультивыборе
    DONE = "done"  # «Готово» в мультивыборе
    SAVE = "save"  # сохранить новую запись после предпросмотра
    CANCEL = "cnl"  # отменить мастер или правку
    ARCHIVE = "arc"  # в архив; у проведения это и есть «убрать из афиши»
    RESTORE = "res"
    DELETE = "del"  # «🗑 Удалить навсегда» из архива: сначала вопрос «точно?»
    PURGE = "purge"  # «Да, удалить» - удаление курса или проведения из архива
    PROGRAM = "prog"  # программа курса постранично, page - страница
    STATUS = "st"  # статус проведения в одно нажатие, value - статус
    COPY = "copy"  # «📋 Скопировать» проведение: мастер спросит только новые даты


class DictCb(CallbackData, prefix="dic"):
    kind: DictKind
    action: DictAction
    item_id: DbInt = 0
    field: str = ""
    page: DbInt = 0
    value: str = ""
