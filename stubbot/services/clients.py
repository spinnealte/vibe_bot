import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import ClientEventType, ConsentType
from stubbot.db.models import Client
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.repositories.clients import ClientRepository
from stubbot.repositories.consents import ConsentRepository
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
        self.consents = ConsentRepository(session)

    async def get_or_create(self, profile: TelegramProfile) -> tuple[Client, bool]:
        """Клиент по telegram_id; создаётся при первом обращении. Второе значение — создан ли сейчас.

        До согласия на обработку ПД из Telegram храним только id (без него бот не может ответить): имя,
        @username и язык запоминаем, когда человек поставит галочку согласия (remember_profile), и дальше
        обновляем при визитах.
        """
        client = await self.clients.get_by_telegram_id(profile.telegram_id)
        if client is not None:
            logger.debug("[DB] Найден клиент #%s", client.id)
            self._refresh_activity(client)
            # Согласие проверяем, только если в Telegram что-то поменялось: лишний запрос на каждое нажатие ни к чему.
            if _snapshot(client) != _profile_fields(profile) and await self.consents.active_consent(
                client.id, ConsentType.PERSONAL_DATA
            ):
                self.remember_profile(client, profile)
            return client, False

        for _ in range(3):
            client = await self.clients.insert_if_absent(
                {
                    "telegram_id": profile.telegram_id,
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

        if created and payload.from_link:
            client.start_param = payload.raw  # хвост ссылки первого захода — как есть, с любым префиксом

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
        # Нераспознанный параметр — произвольный текст, в лог его не пишем.
        shown = payload.raw if payload.kind is not PayloadKind.UNKNOWN else "<нераспознанный параметр>"
        logger.info("Клиент #%s: /start по ссылке %s%s", client.id, shown,
                    " (источник найден)" if source_id else "")

    async def count_referrals(self, client_id: int) -> int:
        return await self.clients.count_referrals(client_id)

    async def mark_bot_blocked(self, telegram_id: int) -> None:
        """Пользователь заблокировал бота (Telegram ответил Forbidden). Снимается при следующем его сообщении."""
        client_id = await self.clients.mark_bot_blocked(telegram_id, datetime.now(UTC))
        if client_id is not None:
            logger.info("Клиент #%s заблокировал бота", client_id)

    @staticmethod
    def remember_profile(client: Client, profile: TelegramProfile) -> None:
        """Имя, @username и язык из Telegram — только для давших согласие на обработку ПД."""
        client.tg_username = profile.username
        client.tg_first_name = profile.first_name
        client.tg_last_name = profile.last_name
        client.tg_language_code = profile.language_code

    @staticmethod
    def _refresh_activity(client: Client) -> None:
        if client.is_bot_blocked:
            # Пишет нам — значит, разблокировал.
            client.is_bot_blocked = False
            client.bot_blocked_at = None
        now = datetime.now(UTC)
        if client.last_seen_at is None or now - client.last_seen_at > LAST_SEEN_THROTTLE:
            client.last_seen_at = now


def _snapshot(client: Client) -> tuple[str | None, ...]:
    return client.tg_username, client.tg_first_name, client.tg_last_name, client.tg_language_code


def _profile_fields(profile: TelegramProfile) -> tuple[str | None, ...]:
    return profile.username, profile.first_name, profile.last_name, profile.language_code
