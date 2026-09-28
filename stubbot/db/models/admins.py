from sqlalchemy import Boolean, String, text
from sqlalchemy.orm import Mapped, mapped_column

from stubbot.db.base import Base, IntPK, TimestampMixin, str_enum
from stubbot.db.enums import AdminRole


class Admin(TimestampMixin, Base):
    """Администраторы бота. Owner при первом запуске берётся из .env (OWNER_TELEGRAM_ID) и засевается скриптом."""

    __tablename__ = "admins"

    id: Mapped[IntPK]
    telegram_id: Mapped[int] = mapped_column(unique=True)
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[AdminRole] = mapped_column(str_enum(AdminRole, "admin_role"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
