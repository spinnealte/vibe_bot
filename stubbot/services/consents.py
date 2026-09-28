from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import ClientEventType, ConsentSource, ConsentType, LegalDocumentType
from stubbot.db.models import LegalDocument
from stubbot.repositories.analytics import AnalyticsRepository
from stubbot.repositories.consents import ConsentRepository

DOCUMENT_FOR_CONSENT = {
    ConsentType.PERSONAL_DATA: LegalDocumentType.PD_CONSENT,
    ConsentType.MARKETING: LegalDocumentType.MARKETING_CONSENT,
}


class ConsentService:
    def __init__(self, session: AsyncSession) -> None:
        self.consents = ConsentRepository(session)
        self.analytics = AnalyticsRepository(session)

    async def current_document(self, consent_type: ConsentType) -> LegalDocument | None:
        return await self.consents.current_document(DOCUMENT_FOR_CONSENT[consent_type])

    async def has_current_consent(self, client_id: int, consent_type: ConsentType) -> bool:
        """Есть действующее согласие именно на текущую версию документа.

        Вышла новая версия — старое согласие не считается, бот попросит согласиться заново.
        """
        document = await self.current_document(consent_type)
        if document is None:
            return False
        consent = await self.consents.active_consent(client_id, consent_type)
        return consent is not None and consent.document_id == document.id

    async def has_any_consent(self, client_id: int, consent_type: ConsentType) -> bool:
        return await self.consents.active_consent(client_id, consent_type) is not None

    async def grant(self, client_id: int, consent_type: ConsentType, document: LegalDocument) -> None:
        now = datetime.now(UTC)
        # Сначала отзываем прежнее (на старую версию), потом добавляем новое: одно активное на тип.
        await self.consents.revoke_active(client_id, consent_type, now)
        self.consents.add(client_id, consent_type, document.id, ConsentSource.TELEGRAM_BOT)
        self.analytics.add_event(
            client_id,
            ClientEventType.CONSENT_GIVEN,
            {"type": consent_type.value, "document_version": document.version},
        )

    async def revoke(self, client_id: int, consent_type: ConsentType) -> None:
        await self.consents.revoke_active(client_id, consent_type, datetime.now(UTC))
