import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import ClientEventType, ProfileStatus
from stubbot.db.models import Client, Position, Specialty
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.repositories.clients import ClientRepository, DictionaryRepository
from stubbot.utils import profile_fields
from stubbot.utils.phone import normalize_phone

logger = logging.getLogger(__name__)


class RegistrationStep(StrEnum):
    PHONE = "phone"
    NAME = "name"
    SPECIALTIES = "specialties"
    POSITIONS = "positions"
    DONE = "done"


class OptionalField(StrEnum):
    """Необязательные поля профиля в порядке вопросов после регистрации. Значение - колонка в clients."""

    CITY = "city"
    WORKPLACE = "workplace_raw"
    EXPERIENCE = "practice_since_year"
    EMAIL = "email"
    BIRTH_DATE = "birth_date"


OPTIONAL_FIELDS_ORDER = list(OptionalField)


class PhoneResult(StrEnum):
    OK = "ok"
    INVALID = "invalid"
    TAKEN = "taken"  # номер уже у другого клиента в боте
    LINKED = "linked"  # номер был у коллеги без Telegram (его записали вдвоём) - его записи теперь у этого клиента


@dataclass(frozen=True)
class PhoneSaved:
    result: PhoneResult
    linked_name: str | None = None  # ФИО, которое ввёл записавший коллега, - подставим на шаге «ФИО»


class RegistrationService:
    """Короткая регистрация. Прогресс сохраняется по шагам: прерванную регистрацию можно продолжить."""

    def __init__(self, session: AsyncSession) -> None:
        self.clients = ClientRepository(session)
        self.dictionaries = DictionaryRepository(session)
        self.analytics = AnalyticsRepository(session)

    async def next_step(self, client: Client) -> RegistrationStep:
        if not client.phone:
            return RegistrationStep.PHONE
        if not (client.full_name and client.name_confirmed_at):
            return RegistrationStep.NAME
        if not await self.clients.has_specialties(client.id):
            return RegistrationStep.SPECIALTIES
        if not await self.clients.has_positions(client.id):
            return RegistrationStep.POSITIONS
        return RegistrationStep.DONE

    async def save_phone(self, client: Client, raw_phone: str, now: datetime | None = None) -> PhoneSaved:
        """Телефон из кнопки «Поделиться номером» - его Telegram отдаёт только со своего аккаунта, номер подтверждён.

        Номер у коллеги без Telegram (его записали вдвоём, полная версия, этап 1, шаг 6) - его записи, заказы
        и листы ожидания переходят этому клиенту, а тот профиль закрывается с пометкой «объединён». Номер
        у другого клиента в боте или у обоих запись на одно проведение - «номер занят», разбирается менеджер.
        """
        phone = normalize_phone(raw_phone)
        if phone is None:
            return PhoneSaved(PhoneResult.INVALID)
        others = [owner for owner in await self.clients.all_by_phone(phone) if owner.id != client.id]
        if any(owner.telegram_id is not None for owner in others):
            logger.warning("Клиент #%s: телефон уже привязан к клиенту #%s", client.id,
                           next(o.id for o in others if o.telegram_id is not None))
            return PhoneSaved(PhoneResult.TAKEN)
        linked_name = None
        if others:
            ids = [owner.id for owner in others]
            if await self.clients.records_overlap(ids, client.id):
                logger.warning("Клиент #%s: телефон у коллеги без Telegram (#%s), но записи пересекаются",
                               client.id, ids)
                return PhoneSaved(PhoneResult.TAKEN)
            linked_name = others[0].full_name
            await self.clients.move_records(ids, client.id)
            # Закрываем сразу запросом: телефон у действующих клиентов уникален - номер освободится раньше,
            # чем его получит этот клиент.
            await self.clients.close_merged(ids, client.id, now or datetime.now(UTC))
            self.analytics.add_event(client.id, ClientEventType.ACCOUNT_LINKED, {"from": ids})
            logger.info("Клиент #%s: телефон коллеги без Telegram - записи профилей %s перенесены", client.id, ids)
        client.phone = phone
        client.phone_confirmed = True
        self._mark_partial(client)
        logger.info("Клиент #%s: сохранён телефон", client.id)
        return PhoneSaved(PhoneResult.LINKED if others else PhoneResult.OK, linked_name)

    def save_name(self, client: Client, full_name: str) -> None:
        client.full_name = full_name
        client.name_confirmed_at = datetime.now(UTC)
        self._mark_partial(client)
        logger.info("Клиент #%s: сохранено ФИО", client.id)

    @staticmethod
    def parse_optional(field: OptionalField, raw: str, today: date) -> str | int | date | None:
        """Разбор ввода пользователя для необязательного поля. None - ввод не подходит."""
        match field:
            case OptionalField.CITY:
                return profile_fields.parse_city(raw)
            case OptionalField.WORKPLACE:
                return profile_fields.parse_workplace(raw)
            case OptionalField.EXPERIENCE:
                return profile_fields.parse_experience_years(raw, today)
            case OptionalField.EMAIL:
                return profile_fields.parse_email(raw)
            case OptionalField.BIRTH_DATE:
                return profile_fields.parse_birth_date(raw, today)

    @staticmethod
    def save_optional(client: Client, field: OptionalField, value: str | int | date | None) -> None:
        """value - результат parse_optional; None очищает поле."""
        setattr(client, field.value, value)
        # Само значение не пишем - это ПД (в старом боте было «на 'asd'»).
        logger.info("Клиент #%s: %s поле '%s'", client.id, "очищено" if value is None else "обновлено", field.value)

    async def specialties(self) -> list[Specialty]:
        return await self.dictionaries.active_specialties()

    async def positions(self) -> list[Position]:
        return await self.dictionaries.active_positions()

    async def selected_specialty_ids(self, client_id: int) -> list[int]:
        return await self.clients.specialty_ids(client_id)

    async def selected_position_ids(self, client_id: int) -> list[int]:
        return await self.clients.position_ids(client_id)

    async def save_specialties(self, client: Client, specialty_ids: list[int]) -> bool:
        """Сохраняется то, что отмечено на экране: id не из действующего справочника отбрасываются (запись ушла
        в архив, пока клиент выбирал, или была у него раньше; подделанная кнопка). False - не осталось ни одной."""
        chosen = set(specialty_ids) & {s.id for s in await self.dictionaries.active_specialties()}
        if not chosen:
            return False
        await self.clients.replace_specialties(client.id, sorted(chosen))
        logger.info("Клиент #%s: сохранены специальности (%d)", client.id, len(chosen))
        return True

    async def save_positions(self, client: Client, position_ids: list[int]) -> bool:
        """Как save_specialties: архивные и подделанные id отбрасываются. False - не осталось ни одной."""
        chosen = set(position_ids) & {p.id for p in await self.dictionaries.active_positions()}
        if not chosen:
            return False
        await self.clients.replace_positions(client.id, sorted(chosen))
        logger.info("Клиент #%s: сохранены должности (%d)", client.id, len(chosen))
        return True

    async def complete(self, client: Client) -> bool:
        """Отмечает профиль заполненным, если пройдены обязательные шаги. True - регистрация завершена именно сейчас
        (тогда предлагаем необязательные поля); False - уже была завершена раньше."""
        if await self.next_step(client) is not RegistrationStep.DONE:
            return False
        if client.profile_status is ProfileStatus.COMPLETED:
            return False
        client.profile_status = ProfileStatus.COMPLETED
        self.analytics.add_event(client.id, ClientEventType.PROFILE_COMPLETED)
        logger.info("Клиент #%s: регистрация завершена", client.id)
        return True

    async def load_profile(self, client_id: int) -> Client:
        return await self.clients.get_with_profile(client_id)

    @staticmethod
    def _mark_partial(client: Client) -> None:
        if client.profile_status is ProfileStatus.NEW:
            client.profile_status = ProfileStatus.PARTIAL
