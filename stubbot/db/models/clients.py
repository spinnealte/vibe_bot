from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from stubbot.db.enums import ProfileStatus

client_specialties = Table(
    "client_specialties",
    Base.metadata,
    Column("client_id", BigInteger, ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True),
    Column("specialty_id", BigInteger, ForeignKey("specialties.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("ix_client_specialties_specialty_id", "specialty_id"),
)

client_positions = Table(
    "client_positions",
    Base.metadata,
    Column("client_id", BigInteger, ForeignKey("clients.id", ondelete="CASCADE"), primary_key=True),
    Column("position_id", BigInteger, ForeignKey("positions.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Index("ix_client_positions_position_id", "position_id"),
)


class Specialty(TimestampMixin, Base):
    __tablename__ = "specialties"

    id: Mapped[IntPK]
    title: Mapped[str] = mapped_column(String(128), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Position(TimestampMixin, Base):
    __tablename__ = "positions"

    id: Mapped[IntPK]
    title: Mapped[str] = mapped_column(String(128), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Client(TimestampMixin, Base):
    __tablename__ = "clients"
    __table_args__ = (
        # Один телефон — один живой клиент; после удаления/анонимизации номер освобождается.
        Index("uq_clients_phone_active", "phone", unique=True, postgresql_where=text("deleted_at IS NULL")),
        CheckConstraint("referred_by_client_id <> id", name="not_self_referred"),
        CheckConstraint("practice_since_year BETWEEN 1950 AND 2100", name="practice_since_year_range"),
    )

    id: Mapped[IntPK]
    # NULL — клиент заведён админом/клиникой и ещё не заходил в бот.
    telegram_id: Mapped[int | None] = mapped_column(unique=True)

    # Снимок профиля Telegram, обновляется при визитах.
    tg_username: Mapped[str | None] = mapped_column(String(64))
    tg_first_name: Mapped[str | None] = mapped_column(String(64))
    tg_last_name: Mapped[str | None] = mapped_column(String(64))
    tg_language_code: Mapped[str | None] = mapped_column(String(16))

    last_name: Mapped[str | None] = mapped_column(String(100))
    first_name: Mapped[str | None] = mapped_column(String(100))
    middle_name: Mapped[str | None] = mapped_column(String(100))
    name_confirmed_at: Mapped[datetime | None]
    birth_date: Mapped[date | None]

    phone: Mapped[str | None] = mapped_column(String(20))  # E.164: +7XXXXXXXXXX
    phone_confirmed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    email: Mapped[str | None] = mapped_column(String(254))
    city: Mapped[str | None] = mapped_column(String(128))
    workplace_raw: Mapped[str | None] = mapped_column(String(256))
    practice_since_year: Mapped[int | None] = mapped_column(SmallInteger)

    profile_status: Mapped[ProfileStatus] = mapped_column(
        str_enum(ProfileStatus, "profile_status"), server_default=ProfileStatus.NEW.value
    )

    referral_code: Mapped[str] = mapped_column(String(32), unique=True)
    referred_by_client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), index=True
    )
    first_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("acquisition_sources.id", ondelete="RESTRICT"), index=True
    )

    is_bot_blocked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    bot_blocked_at: Mapped[datetime | None]
    last_seen_at: Mapped[datetime | None]
    admin_note: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None]

    # lazy="raise": в async-коде связи грузятся только явно (selectinload в репозиториях).
    specialties: Mapped[list[Specialty]] = relationship(secondary=client_specialties, lazy="raise")
    positions: Mapped[list[Position]] = relationship(secondary=client_positions, lazy="raise")


Index("ix_clients_created_at", Client.created_at)
Index("ix_clients_email_lower", func.lower(Client.email))
