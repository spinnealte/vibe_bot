from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from stubbot.db.enums import ConsentSource, ConsentType, LegalDocumentType
from stubbot.db.models import ClientConsent, LegalDocument


class ConsentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current_document(self, doc_type: LegalDocumentType) -> LegalDocument | None:
        return await self.session.scalar(
            select(LegalDocument).where(LegalDocument.type == doc_type, LegalDocument.is_current)
        )

    async def active_consent(self, client_id: int, consent_type: ConsentType) -> ClientConsent | None:
        return await self.session.scalar(
            select(ClientConsent).where(
                ClientConsent.client_id == client_id,
                ClientConsent.type == consent_type,
                ClientConsent.revoked_at.is_(None),
            )
        )

    async def revoke_active(self, client_id: int, consent_type: ConsentType, at: datetime) -> None:
        await self.session.execute(
            update(ClientConsent)
            .where(
                ClientConsent.client_id == client_id,
                ClientConsent.type == consent_type,
                ClientConsent.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )

    def add(self, client_id: int, consent_type: ConsentType, document_id: int, source: ConsentSource) -> None:
        self.session.add(
            ClientConsent(client_id=client_id, type=consent_type, document_id=document_id, source=source)
        )
