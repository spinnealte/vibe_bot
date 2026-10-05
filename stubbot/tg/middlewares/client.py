from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Chat, TelegramObject, User

from stubbot.services.clients import ClientService, TelegramProfile


class ClientMiddleware(BaseMiddleware):
    """Находит или создаёт клиента по отправителю и кладёт в data: `client`, `client_created`.

    Регистрируется outer на message и callback_query — после DbSessionMiddleware (нужна `session`).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        chat: Chat | None = data.get("event_chat")
        # Бот работает только в личных чатах (см. handlers/__init__.py): в группах клиентов не заводим.
        if user is None or user.is_bot or chat is None or chat.type != ChatType.PRIVATE:
            return await handler(event, data)
        profile = TelegramProfile(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
        )
        client, created = await ClientService(data["session"]).get_or_create(profile)
        data["client"] = client
        data["client_created"] = created
        return await handler(event, data)
