import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import ClientEventType
from stubbot.db.models import Client
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.repositories.clients import ClientRepository
from stubbot.services.deeplinks import PayloadKind, StartPayload

logger = logging.getLogger(__name__)

REFERRAL_CODE_BYTES = 6  # token_urlsafe(6) → 8 символов [A-Za-z0-9_-]
LAST_SEEN_THROTTLE = timedelta(minutes=5)


@dataclass(frozen=True)
class TelegramProfile:
    """Снимок пользователя Telegram без зависимости сервисов от aiogram."""

    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None


class ClientService:
    def __init__(self, session: AsyncSession) -> None:
        self.clients = ClientRepository(session)
        self.analytics = AnalyticsRepository(session)

    async def get_or_create(self, profile: TelegramProfile) -> tuple[Client, bool]:
        """Клиент по telegram_id; создаётся при первом обращении. Второе значение — создан ли сейчас."""
        client = await self.clients.get_by_telegram_id(profile.telegram_id)
        if client is not None:
            self._refresh_snapshot(client, profile)
            return client, False

        for _ in range(3):
            client = await self.clients.insert_if_absent(
                {
                    "telegram_id": profile.telegram_id,
                    "tg_username": profile.username,
                    "tg_first_name": profile.first_name,
                    "tg_last_name": profile.last_name,
                    "tg_language_code": profile.language_code,
                    "referral_code": secrets.token_urlsafe(REFERRAL_CODE_BYTES),
                    "last_seen_at": datetime.now(UTC),
                }
            )
            if client is not None:
                logger.info("Новый клиент #%s", client.id)
                return client, True
            # Конфликт: либо параллельный /start уже создал клиента, либо совпал referral_code — пробуем ещё.
            client = await self.clients.get_by_telegram_id(profile.telegram_id)
            if client is not None:
                return client, False
        raise RuntimeError("Не удалось создать клиента: повторные коллизии referral_code")

    async def process_start(self, client: Client, created: bool, payload: StartPayload | None) -> None:
        """Журнал /start и атрибуция: источник трафика и пригласивший (только для новых клиентов)."""
        self.analytics.add_event(
            client.id, ClientEventType.START, {"payload_kind": payload.kind.value if payload else None}
        )
        if payload is None:
            return

        source_id: int | None = None
        if payload.kind is PayloadKind.SOURCE:
            source = await self.analytics.get_active_source(payload.value)
            source_id = source.id if source else None
            if created and source_id is not None:
                client.first_source_id = source_id
        elif payload.kind is PayloadKind.REFERRAL and created:
            referrer = await self.clients.get_by_referral_code(payload.value)
            if referrer is not None and referrer.id != client.id:
                client.referred_by_client_id = referrer.id

        self.analytics.add_touch(client.id, source_id, payload.raw, is_first=created)

    async def count_referrals(self, client_id: int) -> int:
        return await self.clients.count_referrals(client_id)

    @staticmethod
    def _refresh_snapshot(client: Client, profile: TelegramProfile) -> None:
        client.tg_username = profile.username
        client.tg_first_name = profile.first_name
        client.tg_last_name = profile.last_name
        client.tg_language_code = profile.language_code
        if client.is_bot_blocked:
            # Пишет нам — значит, разблокировал.
            client.is_bot_blocked = False
            client.bot_blocked_at = None
        now = datetime.now(UTC)
        if client.last_seen_at is None or now - client.last_seen_at > LAST_SEEN_THROTTLE:
            client.last_seen_at = now
