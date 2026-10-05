"""Выгрузка клиентов в CSV (этап 6): вся таблица клиентов одним файлом для Excel, без аналитики.

В файле персональные данные: он уходит только в чат админа, в лог пишется только число строк.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import ConsentType, ProfileStatus
from stubbot.repositories.clients import ClientRepository
from stubbot.repositories.consents import ConsentRepository
from stubbot.utils.csv_export import build_csv

logger = logging.getLogger(__name__)

# Порядок колонок — как в client_row(). Первая колонка не «ID»: файл, который начинается с этих двух букв,
# Excel принимает за формат SYLK и открывает с ошибкой.
HEADER = (
    "№", "Первый заход", "Последний визит", "Telegram ID", "Username", "Имя в Telegram",
    "ФИО", "ФИО подтверждено", "Телефон", "Email", "Город", "Место работы", "Год начала практики",
    "Дата рождения", "Специальности", "Должности", "Анкета", "Согласие на обработку ПД", "Согласие на рассылку",
    "Хвост ссылки", "Кто пригласил (ID)", "Реферальный код", "Заблокировал бота", "Заметка",
)
PROFILE_STATUS = {
    ProfileStatus.NEW: "не заполнена",
    ProfileStatus.PARTIAL: "частично",
    ProfileStatus.COMPLETED: "заполнена",
}


@dataclass(frozen=True)
class ClientExport:
    filename: str
    content: bytes
    count: int


def _moment(value: datetime | None, tz: ZoneInfo) -> str:
    return value.astimezone(tz).strftime("%d.%m.%Y %H:%M") if value else ""


def _yes_no(value: bool) -> str:
    return "да" if value else "нет"


def client_row(client: Any, pd_consent: bool, marketing: bool, tz: ZoneInfo) -> list[object]:
    """Строка выгрузки. client — Client с загруженными specialties и positions."""
    return [
        client.id,
        _moment(client.created_at, tz),
        _moment(client.last_seen_at, tz),
        client.telegram_id,
        client.tg_username,
        " ".join(filter(None, (client.tg_first_name, client.tg_last_name))),
        client.full_name,
        _yes_no(client.name_confirmed_at is not None),
        client.phone,
        client.email,
        client.city,
        client.workplace_raw,
        client.practice_since_year,
        client.birth_date.strftime("%d.%m.%Y") if client.birth_date else "",
        ", ".join(sorted(s.title for s in client.specialties)),
        ", ".join(sorted(p.title for p in client.positions)),
        PROFILE_STATUS.get(client.profile_status, str(client.profile_status)),
        _yes_no(pd_consent),
        _yes_no(marketing),
        client.start_param,
        client.referred_by_client_id,
        client.referral_code,
        _yes_no(client.is_bot_blocked),
        client.admin_note,
    ]


class ClientExportService:
    def __init__(self, session: AsyncSession, admin_client_id: int) -> None:
        self.clients = ClientRepository(session)
        self.consents = ConsentRepository(session)
        self.admin_client_id = admin_client_id

    async def build(self, now: datetime, tz: ZoneInfo) -> ClientExport:
        clients = await self.clients.all_for_export()
        with_pd = await self.consents.client_ids_with_active(ConsentType.PERSONAL_DATA)
        with_marketing = await self.consents.client_ids_with_active(ConsentType.MARKETING)
        rows = [client_row(client, client.id in with_pd, client.id in with_marketing, tz) for client in clients]
        logger.info("Админ (клиент #%s): выгрузка клиентов, строк: %s", self.admin_client_id, len(rows))
        return ClientExport(
            filename=f"clients_{now.astimezone(tz):%Y-%m-%d_%H-%M}.csv",
            content=build_csv(HEADER, rows),
            count=len(rows),
        )
