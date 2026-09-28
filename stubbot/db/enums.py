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
    INTEREST_CREATED = "interest_created"
    PROFILE_EDITED = "profile_edited"


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


class EnrollmentSource(StrEnum):
    BOT = "bot"
    ADMIN = "admin"
    SITE = "site"
    PHONE = "phone"


class AdminRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    VIEWER = "viewer"
