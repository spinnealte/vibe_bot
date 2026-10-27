from enum import StrEnum


class ProfileStatus(StrEnum):
    NEW = "new"
    PARTIAL = "partial"
    COMPLETED = "completed"


class LegalDocumentType(StrEnum):
    PRIVACY_POLICY = "privacy_policy"
    PD_CONSENT = "pd_consent"
    MARKETING_CONSENT = "marketing_consent"
    OFFER = "offer"


class ConsentType(StrEnum):
    PERSONAL_DATA = "personal_data"
    MARKETING = "marketing"


class ConsentSource(StrEnum):
    TELEGRAM_BOT = "telegram_bot"
    SITE = "site"
    ADMIN = "admin"


class SourceChannel(StrEnum):
    FLYER = "flyer"
    SITE = "site"
    VK = "vk"
    INSTAGRAM = "instagram"
    TG_CHANNEL = "tg_channel"
    REFERRAL = "referral"
    OFFLINE_EVENT = "offline_event"
    OTHER = "other"


class ClientEventType(StrEnum):
    """Хранится как обычный VARCHAR без CHECK: новые типы событий добавляются без миграции."""

    START = "start"
    CONSENT_GIVEN = "consent_given"
    PROFILE_COMPLETED = "profile_completed"
    SCHEDULE_OPENED = "schedule_opened"
    SESSION_VIEWED = "session_viewed"
    PROGRAM_OPENED = "program_opened"
    APPLICATION_CREATED = "application_created"
    APPLICATION_CANCELLED = "application_cancelled"
    INTEREST_CREATED = "interest_created"
    PROFILE_EDITED = "profile_edited"
    ACCOUNT_LINKED = "account_linked"  # записи коллеги без Telegram перешли его аккаунту (по телефону)


class ProgramLevel(StrEnum):
    BASIC = "basic"
    ADVANCED = "advanced"


class DeliveryFormat(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    HYBRID = "hybrid"


class SessionStatus(StrEnum):
    DRAFT = "draft"
    ANNOUNCED = "announced"
    REGISTRATION_OPEN = "registration_open"
    WAITLIST = "waitlist"
    FULL = "full"
    REGISTRATION_CLOSED = "registration_closed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PriceKind(StrEnum):
    FULL = "full"
    THEORY = "theory"
    PRACTICE = "practice"
    GROUP = "group"
    EARLY_BIRD = "early_bird"
    OTHER = "other"


class PriceUnit(StrEnum):
    PER_PERSON = "per_person"
    PER_GROUP = "per_group"


class EnrollmentStatus(StrEnum):
    APPLICATION = "application"
    CONFIRMED = "confirmed"
    WAITLIST = "waitlist"
    CANCELLED_BY_CLIENT = "cancelled_by_client"
    CANCELLED_BY_ADMIN = "cancelled_by_admin"
    ATTENDED = "attended"
    NO_SHOW = "no_show"
    COMPLETED = "completed"


CANCELLED_ENROLLMENT_STATUSES = (EnrollmentStatus.CANCELLED_BY_CLIENT, EnrollmentStatus.CANCELLED_BY_ADMIN)


class PaymentStatus(StrEnum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    REFUNDED = "refunded"


class OrderStatus(StrEnum):
    AWAITING_PAYMENT = "awaiting_payment"  # место занято, ждём оплату (72 часа с формирования заказа)
    PARTIALLY_PAID = "partially_paid"  # оплачено частично - место закреплено, остаток на контроле менеджера
    PAID = "paid"
    CANCELLED = "cancelled"


class CancelChoice(StrEnum):
    """Клиент сам отменил оплаченную (хотя бы частично) запись: что сделать с деньгами. Двигает их админ."""

    TRANSFER = "transfer"  # перенести на другое проведение: запишется заново, оплату засчитает админ
    ADVANCE = "advance"  # оставить на аванс
    REFUND = "refund"  # вернуть деньги - через менеджера


class PayerType(StrEnum):
    INDIVIDUAL = "individual"  # физлицо: счёт в Telegram или на почту из кабинета
    ORGANIZATION = "organization"  # юрлицо: реквизиты файлом, счёт из 1С


class InvoiceChannel(StrEnum):
    """Как ушёл счёт клиенту (решение 27.10.2026): заказ с отметкой - в разделе «📨 Счёт отправлен»."""

    BOT = "bot"  # админ прислал файл счёта через бота, и Telegram его доставил
    MANUAL = "manual"  # счёт отправлен вне бота (почта, 1С) - админ отметил кнопкой; отметку можно снять


class PaymentMethod(StrEnum):
    INVOICE = "invoice"  # по счёту (банковский перевод)
    CASH = "cash"
    CARD = "card"  # картой или по СБП - оплату отметил админ (кнопка «💳 Карта, СБП»)
    CARD_ONLINE = "card_online"  # онлайн через платёжный сервис: картой, SberPay, T-Pay, ЮMoney - всё, кроме СБП
    SBP = "sbp"  # СБП через платёжный сервис
    ADVANCE = "advance"  # из аванса клиента (засчитывает админ)


# Способы, которые админ выбирает кнопкой, когда отмечает оплату (решение 21.10.2026).
MANUAL_PAYMENT_METHODS = (PaymentMethod.INVOICE, PaymentMethod.CASH, PaymentMethod.CARD)


class PaymentProvider(StrEnum):
    MANUAL = "manual"  # оплату отметил админ
    YOOKASSA = "yookassa"
    FAKE = "fake"  # тестовая оплата без ЮKassa (ONLINE_PAYMENTS=fake, только на локальной базе)


class PaymentState(StrEnum):
    PENDING = "pending"  # онлайн-платёж создан, клиент получил ссылку - ждём оплату (в «оплачено» не входит)
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"


class AdvanceReason(StrEnum):
    CANCELLED_ORDER = "cancelled_order"  # + оплаченное по отменённой записи
    APPLIED_TO_ORDER = "applied_to_order"  # - засчитано в оплату заказа
    REFUND = "refund"  # - вернули деньгами
    CORRECTION = "correction"  # +/- правка админа


class EnrollmentSource(StrEnum):
    BOT = "bot"
    ADMIN = "admin"
    SITE = "site"
    PHONE = "phone"


class AdminRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    VIEWER = "viewer"
