from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from stubbot.db.base import Base, IntPK, TimestampMixin, str_enum
from stubbot.db.enums import ConsentSource, ConsentType, LegalDocumentType


class LegalDocument(TimestampMixin, Base):
    """Версия юридического документа. Текст лежит в LEGAL_DOCS_DIR как <type>_v<version>.html."""

    __tablename__ = "legal_documents"
    __table_args__ = (
        UniqueConstraint("type", "version"),
        # Текущей может быть только одна версия каждого типа.
        Index("uq_legal_documents_current_type", "type", unique=True, postgresql_where=text("is_current")),
    )

    id: Mapped[IntPK]
    type: Mapped[LegalDocumentType] = mapped_column(str_enum(LegalDocumentType, "legal_document_type"))
    version: Mapped[str] = mapped_column(String(32))
    url: Mapped[str | None] = mapped_column(String(512))
    text_hash: Mapped[str] = mapped_column(String(64))  # sha256 файла: доказывает, какой именно текст видел клиент
    published_at: Mapped[datetime]
    is_current: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class ClientConsent(TimestampMixin, Base):
    __tablename__ = "client_consents"
    __table_args__ = (
        Index("ix_client_consents_client_id_type", "client_id", "type"),
        # Не больше одного действующего согласия каждого типа; отзыв — через revoked_at, новое согласие — новая строка.
        Index(
            "uq_client_consents_active",
            "client_id",
            "type",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[IntPK]
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    type: Mapped[ConsentType] = mapped_column(str_enum(ConsentType, "consent_type"))
    document_id: Mapped[int] = mapped_column(ForeignKey("legal_documents.id", ondelete="RESTRICT"), index=True)
    granted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None]
    source: Mapped[ConsentSource] = mapped_column(
        str_enum(ConsentSource, "consent_source"), server_default=ConsentSource.TELEGRAM_BOT.value
    )
