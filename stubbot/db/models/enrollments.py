from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from stubbot.db.base import Base, IntPK, TimestampMixin, str_enum
from stubbot.db.enums import (
    CANCELLED_ENROLLMENT_STATUSES,
    EnrollmentSource,
    EnrollmentStatus,
    PaymentStatus,
)

_cancelled_sql = ", ".join(f"'{status.value}'" for status in CANCELLED_ENROLLMENT_STATUSES)


class Enrollment(TimestampMixin, Base):
    """Заявка/место конкретного врача на потоке."""

    __tablename__ = "enrollments"
    __table_args__ = (
        # Одна неотменённая заявка клиента на поток; после отмены можно подать заново.
        Index(
            "uq_enrollments_client_id_session_id_active",
            "client_id",
            "session_id",
            unique=True,
            postgresql_where=text(f"status NOT IN ({_cancelled_sql})"),
        ),
        CheckConstraint("requested_seats >= 1", name="requested_seats_positive"),
    )

    id: Mapped[IntPK]
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="RESTRICT"), index=True)
    price_option_id: Mapped[int | None] = mapped_column(ForeignKey("price_options.id", ondelete="RESTRICT"))
    status: Mapped[EnrollmentStatus] = mapped_column(
        str_enum(EnrollmentStatus, "enrollment_status"), server_default=EnrollmentStatus.APPLICATION.value, index=True
    )
    # Кэш; в полном боте считается из orders.
    payment_status: Mapped[PaymentStatus] = mapped_column(
        str_enum(PaymentStatus, "payment_status"), server_default=PaymentStatus.UNPAID.value
    )
    # MVP: «записываюсь с коллегами», менеджер обрабатывает вручную.
    requested_seats: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    source: Mapped[EnrollmentSource] = mapped_column(
        str_enum(EnrollmentSource, "enrollment_source"), server_default=EnrollmentSource.BOT.value
    )
    client_comment: Mapped[str | None] = mapped_column(Text)
    admin_comment: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]
    cancel_reason: Mapped[str | None] = mapped_column(String(512))


class ProgramInterest(TimestampMixin, Base):
    """«Сообщите о наборе» по курсу."""

    __tablename__ = "program_interests"
    __table_args__ = (UniqueConstraint("client_id", "program_id"),)

    id: Mapped[IntPK]
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id", ondelete="RESTRICT"), index=True)
    notified_at: Mapped[datetime | None]
