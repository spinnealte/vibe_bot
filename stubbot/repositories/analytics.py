from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import ClientEventType
from stubbot.db.models import AcquisitionSource, ClientEvent, ClientSourceTouch


class AnalyticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_source(self, code: str) -> AcquisitionSource | None:
        return await self.session.scalar(
            select(AcquisitionSource).where(AcquisitionSource.code == code, AcquisitionSource.is_active)
        )

    def add_touch(self, client_id: int, source_id: int | None, raw_payload: str, is_first: bool) -> None:
        self.session.add(
            ClientSourceTouch(client_id=client_id, source_id=source_id, raw_payload=raw_payload, is_first=is_first)
        )

    def add_event(self, client_id: int, event_type: ClientEventType, payload: dict[str, Any] | None = None) -> None:
        self.session.add(ClientEvent(client_id=client_id, type=event_type.value, payload=payload or {}))
