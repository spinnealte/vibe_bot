from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import ClientEventType, ProfileStatus
from stubbot.db.models import Client, Position, Specialty
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.repositories.clients import ClientRepository, DictionaryRepository
from stubbot.utils.phone import normalize_phone


class RegistrationStep(StrEnum):
    PHONE = "phone"
    NAME = "name"
    SPECIALTIES = "specialties"
    POSITIONS = "positions"
    DONE = "done"


class OptionalField(StrEnum):
    """Необязательные поля профиля в порядке вопросов после регистрации. Значение — колонка в clients."""

    CITY = "city"
    WORKPLACE = "workplace_raw"
    EXPERIENCE = "practice_since_year"
    EMAIL = "email"
    BIRTH_DATE = "birth_date"


OPTIONAL_FIELDS_ORDER = list(OptionalField)


class PhoneResult(StrEnum):
    OK = "ok"
    INVALID = "invalid"
    TAKEN = "taken"  # номер уже у другого клиента


class RegistrationService:
    """Короткая регистрация. Прогресс сохраняется по шагам: прерванную регистрацию можно продолжить."""

    def __init__(self, session: AsyncSession) -> None:
        self.clients = ClientRepository(session)
        self.dictionaries = DictionaryRepository(session)
        self.analytics = AnalyticsRepository(session)

    async def next_step(self, client: Client) -> RegistrationStep:
        if not client.phone:
            return RegistrationStep.PHONE
        if not (client.last_name and client.first_name and client.name_confirmed_at):
            return RegistrationStep.NAME
        if not await self.clients.has_specialties(client.id):
            return RegistrationStep.SPECIALTIES
        if not await self.clients.has_positions(client.id):
            return RegistrationStep.POSITIONS
        return RegistrationStep.DONE

    async def save_phone(self, client: Client, raw_phone: str) -> PhoneResult:
        phone = normalize_phone(raw_phone)
        if phone is None:
            return PhoneResult.INVALID
        owner = await self.clients.get_by_phone(phone)
        if owner is not None and owner.id != client.id:
            return PhoneResult.TAKEN
        client.phone = phone
        client.phone_confirmed = True
        self._mark_partial(client)
        return PhoneResult.OK

    def save_name(self, client: Client, last_name: str, first_name: str, middle_name: str | None) -> None:
        client.last_name = last_name
        client.first_name = first_name
        client.middle_name = middle_name
        client.name_confirmed_at = datetime.now(UTC)
        self._mark_partial(client)

    @staticmethod
    def save_optional(client: Client, field: OptionalField, value: str | int | date) -> None:
        """Значение уже разобрано и проверено (utils/profile_fields.py)."""
        setattr(client, field.value, value)

    async def specialties(self) -> list[Specialty]:
        return await self.dictionaries.active_specialties()

    async def positions(self) -> list[Position]:
        return await self.dictionaries.active_positions()

    async def selected_specialty_ids(self, client_id: int) -> list[int]:
        return await self.clients.specialty_ids(client_id)

    async def selected_position_ids(self, client_id: int) -> list[int]:
        return await self.clients.position_ids(client_id)

    async def save_specialties(self, client: Client, specialty_ids: list[int]) -> bool:
        """False — пустой выбор или id не из активного справочника (подделанный callback)."""
        allowed = {s.id for s in await self.dictionaries.active_specialties()}
        if not specialty_ids or not set(specialty_ids) <= allowed:
            return False
        await self.clients.replace_specialties(client.id, sorted(set(specialty_ids)))
        return True

    async def save_positions(self, client: Client, position_ids: list[int]) -> bool:
        allowed = {p.id for p in await self.dictionaries.active_positions()}
        if not position_ids or not set(position_ids) <= allowed:
            return False
        await self.clients.replace_positions(client.id, sorted(set(position_ids)))
        return True

    async def complete(self, client: Client) -> bool:
        """Отмечает профиль заполненным, если пройдены обязательные шаги. True — регистрация завершена именно сейчас
        (тогда предлагаем необязательные поля); False — уже была завершена раньше."""
        if await self.next_step(client) is not RegistrationStep.DONE:
            return False
        if client.profile_status is ProfileStatus.COMPLETED:
            return False
        client.profile_status = ProfileStatus.COMPLETED
        self.analytics.add_event(client.id, ClientEventType.PROFILE_COMPLETED)
        return True

    async def load_profile(self, client_id: int) -> Client:
        return await self.clients.get_with_profile(client_id)

    @staticmethod
    def _mark_partial(client: Client) -> None:
        if client.profile_status is ProfileStatus.NEW:
            client.profile_status = ProfileStatus.PARTIAL
