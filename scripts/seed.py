"""Начальные данные: справочники, owner, временные юридические документы. Безопасно запускать повторно.

Запуск из корня stubBot: python -m scripts.seed
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from stubbot.config import get_settings
from stubbot.db.enums import AdminRole, LegalDocumentType
from stubbot.db.models import Admin, LegalDocument, Position, Specialty
from stubbot.db.session import create_engine, create_session_factory
from stubbot.services.legal import text_hash

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

# Простые заглушки (версия «0» — черновик), пока нет настоящих текстов. Черновик сиды перезаписывают на месте;
# настоящие тексты выпускаются версией «1» и дальше (см. legal/README.md) — тогда бот попросит согласие заново.
PLACEHOLDER_DOCUMENTS = {
    LegalDocumentType.PD_CONSENT: (
        "<b>Согласие на обработку персональных данных</b>\n\n"
        "Нажимая «✅ Принимаю», я соглашаюсь на обработку моих персональных данных учебным центром."
    ),
    LegalDocumentType.MARKETING_CONSENT: (
        "<b>Анонсы курсов</b>\n\n"
        "Присылать Вам анонсы новых курсов? Отказаться можно в любой момент в личном кабинете."
    ),
    LegalDocumentType.PRIVACY_POLICY: (
        "<b>Политика обработки персональных данных</b>\n\n"
        "Текст политики появится позже."
    ),
}
PLACEHOLDER_VERSION = "0"


def _catalog_rows(titles: list[str]) -> list[dict[str, object]]:
    return [{"title": title, "sort_order": (i + 1) * 10} for i, title in enumerate(titles)]


def _write_placeholder_files(docs_dir: Path) -> dict[LegalDocumentType, str]:
    """Пишет черновики (версия «0») в LEGAL_DOCS_DIR — всегда актуальным текстом. Возвращает их хеши."""
    docs_dir.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for doc_type, content in PLACEHOLDER_DOCUMENTS.items():
        path = docs_dir / f"{doc_type.value}_v{PLACEHOLDER_VERSION}.html"
        # write_bytes, а не write_text: на Windows write_text превратил бы \n в \r\n.
        path.write_bytes((content + "\n").encode("utf-8"))
        hashes[doc_type] = text_hash(path.read_bytes())
    return hashes


async def seed() -> None:
    settings = get_settings()
    hashes = _write_placeholder_files(settings.legal_docs_dir)
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
            now = datetime.now(UTC)
            for doc_type, digest in hashes.items():
                # Черновик «0» уже есть — обновляем хеш: текст поменялся на месте, согласия на «0» остаются в силе.
                await session.execute(
                    update(LegalDocument)
                    .where(LegalDocument.type == doc_type, LegalDocument.version == PLACEHOLDER_VERSION)
                    .values(text_hash=digest)
                )
                # Без цели конфликта: не добавляем, если «0» уже есть или текущей стала настоящая версия.
                await session.execute(
                    insert(LegalDocument)
                    .values(type=doc_type, version=PLACEHOLDER_VERSION, text_hash=digest,
                            published_at=now, is_current=True)
                    .on_conflict_do_nothing()
                )
    finally:
        await engine.dispose()
    print("Сиды применены: специальности, должности, owner, временные документы (версия 0).")


if __name__ == "__main__":
    asyncio.run(seed())
