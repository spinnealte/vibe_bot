import datetime as dt
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from stubbot.db.base import Base, IntPK, TimestampMixin, str_enum
from stubbot.db.enums import DeliveryFormat, PriceKind, PriceUnit, ProgramLevel, SessionStatus
from stubbot.db.models.clients import Specialty

program_specialties = Table(
    "program_specialties",
    Base.metadata,
    Column("program_id", BigInteger, ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True),
    Column("specialty_id", BigInteger, ForeignKey("specialties.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("ix_program_specialties_specialty_id", "specialty_id"),
)

# Лекторы — у курса, а не у потока: курс авторский, другой лектор его не читает (решение 30.09.2026).
program_lecturers = Table(
    "program_lecturers",
    Base.metadata,
    Column("program_id", BigInteger, ForeignKey("programs.id", ondelete="CASCADE"), primary_key=True),
    Column("lecturer_id", BigInteger, ForeignKey("lecturers.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("ix_program_lecturers_lecturer_id", "lecturer_id"),
)


class Venue(TimestampMixin, Base):
    """Площадка. Город по умолчанию — Санкт-Петербург, отдельно не хранится."""

    __tablename__ = "venues"

    id: Mapped[IntPK]
    name: Mapped[str] = mapped_column(String(128))
    address: Mapped[str] = mapped_column(String(256))
    map_url: Mapped[str | None] = mapped_column(String(512))
    directions_text: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Lecturer(TimestampMixin, Base):
    __tablename__ = "lecturers"

    id: Mapped[IntPK]
    full_name: Mapped[str] = mapped_column(String(300))  # «Фамилия Имя Отчество»; в карточке — «Фамилия И. О.»
    regalia: Mapped[str | None] = mapped_column(Text)
    bio_html: Mapped[str | None] = mapped_column(Text)
    photo_file_id: Mapped[str | None] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class Program(TimestampMixin, Base):
    """Курс как продукт. Конкретные проведения — CourseSession."""

    __tablename__ = "programs"
    __table_args__ = (
        CheckConstraint("duration_hours > 0", name="duration_hours_positive"),
        CheckConstraint("nmo_points >= 0", name="nmo_points_non_negative"),
    )

    id: Mapped[IntPK]
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(256))
    short_description: Mapped[str | None] = mapped_column(String(512))
    description_html: Mapped[str | None] = mapped_column(Text)
    program_html: Mapped[str | None] = mapped_column(Text)
    level: Mapped[ProgramLevel | None] = mapped_column(str_enum(ProgramLevel, "program_level"))
    default_format: Mapped[DeliveryFormat] = mapped_column(
        str_enum(DeliveryFormat, "default_format"), server_default=DeliveryFormat.OFFLINE.value
    )
    duration_hours: Mapped[int | None] = mapped_column(SmallInteger)
    cover_file_id: Mapped[str | None] = mapped_column(String(256))
    nmo_points: Mapped[int | None] = mapped_column(SmallInteger)
    is_published: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Показывать в афише без ближайших проведений («даты уточняются»). Новое проведение включает обратно.
    show_without_dates: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    archived_at: Mapped[datetime | None]
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    specialties: Mapped[list[Specialty]] = relationship(secondary=program_specialties, lazy="raise")
    lecturers: Mapped[list[Lecturer]] = relationship(secondary=program_lecturers, lazy="raise")
    sessions: Mapped[list["CourseSession"]] = relationship(back_populates="program", lazy="raise")


class CourseSession(TimestampMixin, Base):
    """Поток — конкретное проведение курса. Класс не называется Session, чтобы не путать с сессией SQLAlchemy."""

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_status_start_date", "status", "start_date"),
        CheckConstraint("end_date >= start_date", name="dates_order"),
        CheckConstraint("capacity > 0", name="capacity_positive"),
    )

    id: Mapped[IntPK]
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id", ondelete="RESTRICT"), index=True)
    status: Mapped[SessionStatus] = mapped_column(
        str_enum(SessionStatus, "session_status"), server_default=SessionStatus.DRAFT.value
    )
    format: Mapped[DeliveryFormat] = mapped_column(str_enum(DeliveryFormat, "session_format"))
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id", ondelete="RESTRICT"), index=True)
    online_url: Mapped[str | None] = mapped_column(String(512))
    # Кэш по session_days: первый и последний день, поддерживается сервисом при изменении дней.
    start_date: Mapped[date]
    end_date: Mapped[date]
    registration_deadline: Mapped[datetime | None]
    capacity: Mapped[int | None] = mapped_column(Integer)
    title_override: Mapped[str | None] = mapped_column(String(256))
    cover_file_id: Mapped[str | None] = mapped_column(String(256))
    is_visible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    program: Mapped[Program] = relationship(back_populates="sessions", lazy="raise")
    venue: Mapped[Venue | None] = relationship(lazy="raise")
    days: Mapped[list["SessionDay"]] = relationship(
        back_populates="session",
        order_by=lambda: [SessionDay.date, SessionDay.start_time],
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    price_options: Mapped[list["PriceOption"]] = relationship(
        back_populates="session", order_by=lambda: PriceOption.sort_order, lazy="raise"
    )


class SessionDay(TimestampMixin, Base):
    """День потока. На одну дату может быть несколько строк (например, теория утром и практика в другом месте)."""

    __tablename__ = "session_days"
    __table_args__ = (
        Index("ix_session_days_session_id_date", "session_id", "date"),
        CheckConstraint("end_time > start_time", name="times_order"),
    )

    id: Mapped[IntPK]
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    date: Mapped[dt.date]  # dt.date: имя поля совпадает с типом
    # Местное время площадки (Europe/Moscow), без часового пояса.
    start_time: Mapped[time | None]
    end_time: Mapped[time | None]
    topic: Mapped[str | None] = mapped_column(String(256))
    # NULL — та же площадка, что у потока.
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venues.id", ondelete="RESTRICT"), index=True)

    session: Mapped[CourseSession] = relationship(back_populates="days", lazy="raise")
    venue: Mapped[Venue | None] = relationship(lazy="raise")


class PriceOption(TimestampMixin, Base):
    """Тариф потока. «Для двух коллег» = kind=group, unit=per_group, min_seats=max_seats=2."""

    __tablename__ = "price_options"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint("min_seats >= 1", name="min_seats_positive"),
        CheckConstraint("max_seats >= min_seats", name="seats_range"),
        CheckConstraint("seats_limit > 0", name="seats_limit_positive"),
        CheckConstraint("valid_until > valid_from", name="validity_order"),
    )

    id: Mapped[IntPK]
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[PriceKind] = mapped_column(str_enum(PriceKind, "price_kind"))
    label: Mapped[str] = mapped_column(String(128))
    amount: Mapped[int] = mapped_column(Integer)  # копейки
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'RUB'"))
    unit: Mapped[PriceUnit] = mapped_column(str_enum(PriceUnit, "price_unit"), server_default=PriceUnit.PER_PERSON.value)
    min_seats: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    max_seats: Mapped[int | None] = mapped_column(SmallInteger)
    valid_from: Mapped[datetime | None]
    valid_until: Mapped[datetime | None]
    seats_limit: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"))

    session: Mapped[CourseSession] = relationship(back_populates="price_options", lazy="raise")
