"""Текст программы курса из файла: .docx (жирный, курсив, списки, заголовки) или .txt → HTML для Telegram."""

import re
from html import escape
from io import BytesIO

from docx import Document
from docx.text.paragraph import Paragraph
from docx.text.run import Run

SUPPORTED_EXTENSIONS = (".docx", ".txt")
MAX_FILE_SIZE = 2 * 1024 * 1024
_BLANK_LINES = re.compile(r"\n{3,}")


def file_to_html(filename: str, content: bytes) -> str | None:
    """None — формат не поддерживается или файл не читается."""
    name = filename.lower()
    try:
        if name.endswith(".docx"):
            return _docx_to_html(content)
        if name.endswith(".txt"):
            return _txt_to_html(content)
    except Exception:  # битый файл — сообщим админу, не роняя апдейт
        return None
    return None


def _txt_to_html(content: bytes) -> str:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1251")  # «Блокнот» в старых Windows
    return _tidy(escape(text.replace("\r\n", "\n"), quote=False))


def _docx_to_html(content: bytes) -> str:
    lines = [_paragraph_html(p) for p in Document(BytesIO(content)).paragraphs]
    return _tidy("\n".join(lines))


def _paragraph_html(paragraph: Paragraph) -> str:
    if not paragraph.text.strip():
        return ""
    style = (paragraph.style.name if paragraph.style is not None else "").lower()
    if "heading" in style or "заголовок" in style or style == "title":
        return f"<b>{escape(paragraph.text.strip(), quote=False)}</b>"
    html = "".join(_run_html(run) for run in paragraph.runs).strip()
    numbered = paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None  # маркеры и нумерация Word
    return f"• {html}" if numbered or "list" in style or "списка" in style else html


def _run_html(run: Run) -> str:
    text = escape(run.text, quote=False)
    if not text.strip():
        return text
    if run.bold:
        text = f"<b>{text}</b>"
    if run.italic:
        text = f"<i>{text}</i>"
    if run.underline:
        text = f"<u>{text}</u>"
    return text


def _tidy(html: str) -> str:
    return _BLANK_LINES.sub("\n\n", html).strip()
