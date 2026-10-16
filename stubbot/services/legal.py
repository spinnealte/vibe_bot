"""Юридические документы бота: что человек видит в боте и на что соглашается.

Полные документы — на сайте учебного центра (url), их копии на дату выпуска версии — в legal/site/. Тексты,
которые показывает бот, лежат здесь, в коде, вместе с номером версии: так они попадают на сервер вместе с ботом
(на Amvera нет консоли, чтобы выкладывать файлы), а в git видно, что и когда менялось.

Новый текст — новая версия. При запуске бот сам заводит её в legal_documents и делает текущей (publish_documents),
и все, кто соглашался с прежней, увидят согласие заново. Текст поменяли, а версию оставили — бот не запустится:
согласия в базе ссылались бы не на тот текст, который видел человек.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import LegalDocumentType
from stubbot.db.models import LegalDocument
from stubbot.repositories.consents import ConsentRepository

logger = logging.getLogger(__name__)

# Документы ООО «АкваСтом Груп» на сайте; заказчик одобрил их для бота 16.10.2026.
POLICY_URL = "https://akvastom-group.ru/privacy/"
CONSENT_URL = "https://akvastom-group.ru/privacy/consent/"


@dataclass(frozen=True)
class LegalText:
    type: LegalDocumentType
    version: str
    url: str
    html: str  # что показывает бот (HTML Telegram); его хеш хранится в legal_documents.text_hash

    @property
    def text_hash(self) -> str:
        return hashlib.sha256(self.html.encode("utf-8")).hexdigest()

    @property
    def name(self) -> str:
        return f"{self.type.value} v{self.version}"


# Формулировка заказчика (16.10.2026): одна обязательная галочка — политика, согласие на обработку ПД
# и информационные сообщения. Ссылки ведут на документы на сайте.
PD_CONSENT = LegalText(
    LegalDocumentType.PD_CONSENT, "1", CONSENT_URL,
    f'Я согласен с <a href="{POLICY_URL}">Политикой обработки персональных данных</a>, даю '
    f'<a href="{CONSENT_URL}">согласие на обработку моих персональных данных</a> и на получение '
    "информационных сообщений.",
)
# Отдельно в боте не показывается — на неё ведёт ссылка из согласия на ПД.
PRIVACY_POLICY = LegalText(
    LegalDocumentType.PRIVACY_POLICY, "1", POLICY_URL,
    f'<a href="{POLICY_URL}">Политика обработки персональных данных</a>',
)
# Реклама — отдельное добровольное согласие (закон о рекламе): спрашивается после согласия на ПД и включается
# или выключается в кабинете. Текст согласия — во второй части страницы согласия на сайте.
MARKETING_CONSENT = LegalText(
    LegalDocumentType.MARKETING_CONSENT, "1", CONSENT_URL,
    "🔔 <b>Анонсы новых курсов</b>\n\n"
    "Присылать Вам анонсы новых курсов, специальные предложения и приглашения на мероприятия учебного центра?\n"
    "Это по желанию: отказаться можно в любой момент в личном кабинете.\n\n"
    f'<i>Нажимая «🔔 Да, присылать», Вы даёте <a href="{CONSENT_URL}">согласие на получение рекламных '
    "рассылок</a>.</i>",
)
DOCUMENTS = (PRIVACY_POLICY, PD_CONSENT, MARKETING_CONSENT)


class LegalTextChanged(RuntimeError):
    """Текст документа в коде изменили, а номер версии оставили прежним."""


def document_text(doc: LegalDocument) -> str | None:
    """Текст версии документа, как его показывает бот. None — такой версии в коде нет (согласие собирать нельзя)."""
    known = next((d for d in DOCUMENTS if d.type is doc.type and d.version == doc.version), None)
    return known.html if known is not None else None


async def publish_documents(session: AsyncSession, now: datetime) -> list[str]:
    """Завести в legal_documents версии из кода, которых там ещё нет, и сделать их текущими.

    Безопасно запускать при каждом старте: уже выпущенная версия не трогается. Возвращает выпущенные сейчас
    («pd_consent v1»). LegalTextChanged — текст версии в коде не совпадает с тем, что уже выпущен.
    """
    repo = ConsentRepository(session)
    published = []
    for text in DOCUMENTS:
        row = await repo.document(text.type, text.version)
        if row is not None and row.text_hash != text.text_hash:
            raise LegalTextChanged(
                f"Текст {text.name} изменён, а версия прежняя — увеличьте version в stubbot/services/legal.py"
            )
        if row is not None and row.is_current:
            continue
        # Текущая версия у типа одна (частичный UNIQUE): сначала снимаем отметку с прежней.
        await repo.unset_current(text.type)
        if row is None:
            repo.add_document(LegalDocument(type=text.type, version=text.version, url=text.url,
                                            text_hash=text.text_hash, published_at=now, is_current=True))
            published.append(text.name)
            logger.info("Выпущен документ %s", text.name)
        else:
            row.is_current = True
            logger.info("Документ %s снова текущий", text.name)
    return published
