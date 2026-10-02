from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.models import Lecturer, Position, Specialty, Venue

DictionaryItem = Lecturer | Venue | Specialty | Position


class AdminDictionaryRepository:
    """Общий доступ к справочнику для админки. Модель и поле-название задаёт описание справочника."""

    def __init__(self, session: AsyncSession, model: type[DictionaryItem], title_field: str) -> None:
        self.session = session
        self.model = model
        self.title = getattr(model, title_field)

    async def list_all(self) -> list[DictionaryItem]:
        """Действующие сначала, архив — в конце; внутри — по названию."""
        stmt = select(self.model).order_by(self.model.is_active.desc(), self.title)
        return list(await self.session.scalars(stmt))

    async def get(self, item_id: int) -> DictionaryItem | None:
        return await self.session.get(self.model, item_id)

    async def add(self, values: dict[str, Any]) -> DictionaryItem:
        item = self.model(**values)
        self.session.add(item)
        await self.session.flush()  # нужен id для карточки
        return item

    async def title_taken(self, title: str, exclude_id: int | None = None) -> bool:
        stmt = select(self.model.id).where(func.lower(self.title) == title.lower())
        if exclude_id is not None:
            stmt = stmt.where(self.model.id != exclude_id)
        return await self.session.scalar(stmt.limit(1)) is not None

    async def next_sort_order(self) -> int:
        current = await self.session.scalar(select(func.max(self.model.sort_order)))
        return (current or 0) + 10
