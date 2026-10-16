"""Начальные данные: справочники, owner, текущие версии юридических документов. Безопасно запускать повторно.

Запуск из корня stubBot: python -m scripts.seed
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from stubbot.config import get_settings
from stubbot.db.enums import AdminRole
from stubbot.db.models import Admin, Position, Specialty
from stubbot.db.session import create_engine, create_session_factory
from stubbot.services.legal import publish_documents

SPECIALTIES = [
    "Хирургия",
    "Имплантация",
    "ЧЛХ",
    "Анестезия",
    "Ортопедия",
    "Гнатология",
    "Ортодонтия",
    "Пародонтология",
    "Терапия",
    "Гигиена",
    "Зуботехника",
    "Управление",
    "Сервис",
]

POSITIONS = [
    "Главный врач",
    "Директор",
    "Заместитель директора",
    "Врач",
    "Ассистент врача",
    "Медсестра / Медбрат",
    "Администратор",
    "Старший администратор",
    "Менеджер",
    "Владелец",
    "Учредитель",
]

def _catalog_rows(titles: list[str]) -> list[dict[str, object]]:
    return [{"title": title, "sort_order": (i + 1) * 10} for i, title in enumerate(titles)]


async def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session, session.begin():
            await session.execute(
                insert(Specialty).values(_catalog_rows(SPECIALTIES)).on_conflict_do_nothing(index_elements=["title"])
            )
            await session.execute(
                insert(Position).values(_catalog_rows(POSITIONS)).on_conflict_do_nothing(index_elements=["title"])
            )
            await session.execute(
                insert(Admin)
                .values(telegram_id=settings.owner_telegram_id, name="Owner", role=AdminRole.OWNER)
                .on_conflict_do_nothing(index_elements=["telegram_id"])
            )
            # То же делает бот при каждом запуске; здесь - чтобы база была готова и до первого запуска.
            published = await publish_documents(session, datetime.now(UTC))
    finally:
        await engine.dispose()
    print("Сиды применены: специальности, должности, owner, документы"
          + (f" (выпущены: {', '.join(published)})." if published else " (уже выпущены)."))


if __name__ == "__main__":
    asyncio.run(seed())
