import hashlib
import logging
from pathlib import Path

from stubbot.db.models import LegalDocument

logger = logging.getLogger(__name__)

MISSING_TEXT = "Текст документа временно недоступен. Попробуйте позже."


def document_path(docs_dir: Path, doc: LegalDocument) -> Path:
    return docs_dir / f"{doc.type.value}_v{doc.version}.html"


def text_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_document_text(docs_dir: Path, doc: LegalDocument) -> str | None:
    """Текст версии документа из LEGAL_DOCS_DIR. None — файла нет (согласие собирать нельзя)."""
    path = document_path(docs_dir, doc)
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        logger.error("Нет файла юридического документа: %s", path)
        return None
    if text_hash(content) != doc.text_hash:
        # Файл правили на месте вместо выпуска новой версии: согласия ссылаются на другой текст.
        logger.warning("Текст %s не совпадает с хешем в legal_documents #%s", path.name, doc.id)
    # Файл могли сохранить в Windows-редакторе: \r\n → \n, иначе в Telegram лишние переводы строк.
    return content.decode("utf-8").replace("\r\n", "\n").strip()
