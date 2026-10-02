from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject, User

from stubbot.config import Settings


class IsAdmin(BaseFilter):
    """Админ — owner или id из ADMIN_IDS (.env). Ставится на весь админ-роутер: message и callback_query."""

    async def __call__(self, event: TelegramObject, settings: Settings, event_from_user: User | None = None) -> bool:
        return event_from_user is not None and settings.is_admin(event_from_user.id)
