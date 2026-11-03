"""Файлы от клиентов: реквизиты организации (бот и веб-приложение).

Файл уходит админам и открывается у них на компьютере - поэтому только документы и картинки, а не что угодно
(ревью 03.11.2026: раньше принимался любой файл, в том числе исполняемый).
"""

from pathlib import PurePath

REQUISITES_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods", ".rtf", ".txt",
                         ".jpg", ".jpeg", ".png", ".heic", ".webp")
MAX_REQUISITES_SIZE = 20 * 1024 * 1024  # 20 МБ - столько бот может скачать через Bot API


def requisites_file_ok(file_name: str | None, size: int | None) -> bool:
    """Подходит ли файл реквизитов: известный тип документа или картинки и не больше MAX_REQUISITES_SIZE."""
    if not file_name or PurePath(file_name).suffix.lower() not in REQUISITES_EXTENSIONS:
        return False
    return size is None or 0 < size <= MAX_REQUISITES_SIZE
