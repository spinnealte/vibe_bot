from typing import Any

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from stubbot.db.base import Base, CreatedAtMixin, IntPK, TimestampMixin, str_enum
from stubbot.db.enums import SourceChannel


class AcquisitionSource(TimestampMixin, Base):
    """Источник трафика. Диплинк: t.me/<bot>?start=src_<code>."""

    __tablename__ = "acquisition_sources"
    __table_args__ = (CheckConstraint("code ~ '^[A-Za-z0-9_-]{1,50}$'", name="code_format"),)

    id: Mapped[IntPK]
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    channel: Mapped[SourceChannel] = mapped_column(str_enum(SourceChannel, "source_channel"))
    campaign: Mapped[str | None] = mapped_column(String(128))
    placement: Mapped[str | None] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class ClientSourceTouch(CreatedAtMixin, Base):
    """Каждый заход по диплинку, в том числе уже зарегистрированных. Строки не меняются — только created_at."""

    __tablename__ = "client_source_touches"
    __table_args__ = (Index("ix_client_source_touches_source_id_created_at", "source_id", "created_at"),)

    id: Mapped[IntPK]
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    # NULL — код не распознан (опечатка, удалённая кампания); сам payload сохраняется в raw_payload.
    source_id: Mapped[int | None] = mapped_column(ForeignKey("acquisition_sources.id", ondelete="RESTRICT"))
    raw_payload: Mapped[str] = mapped_column(String(64))
    is_first: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class ClientEvent(CreatedAtMixin, Base):
    """Журнал действий для воронки. type — значения ClientEventType, без CHECK."""

    __tablename__ = "client_events"
    __table_args__ = (
        Index("ix_client_events_type_created_at", "type", "created_at"),
        Index("ix_client_events_client_id_created_at", "client_id", "created_at"),
    )

    id: Mapped[IntPK]
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
