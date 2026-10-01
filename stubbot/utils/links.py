import re
from urllib.parse import quote, urlsplit

_TME_HOSTS = ("t.me", "telegram.me")
_USERNAME_PATH = re.compile(r"/[A-Za-z0-9_]{4,32}/?")


def with_draft_text(url: str, text: str) -> str:
    """Ссылка на чат https://t.me/<username> + ?text=… — Telegram подставит текст черновиком в поле ввода.

    Другие ссылки (tg://, приглашения, ссылки уже с параметрами) возвращаются как есть: им черновик не дописываем.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc not in _TME_HOSTS or parts.query or parts.fragment:
        return url
    if not _USERNAME_PATH.fullmatch(parts.path):
        return url
    return f"{url.rstrip('/')}?text={quote(text, safe='')}"
