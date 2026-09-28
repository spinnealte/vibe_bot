"""Разбор параметра /start: t.me/<bot>?start=src_<code> | ref_<code> | inv_<token>."""

import re
from dataclasses import dataclass
from enum import StrEnum

MAX_PAYLOAD_LENGTH = 64  # лимит Telegram на start-параметр
_VALUE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")


class PayloadKind(StrEnum):
    SOURCE = "src"
    REFERRAL = "ref"
    INVITE = "inv"  # приглашение от клиники — полный бот
    UNKNOWN = "unknown"


_PREFIXES = {kind.value: kind for kind in (PayloadKind.SOURCE, PayloadKind.REFERRAL, PayloadKind.INVITE)}


@dataclass(frozen=True)
class StartPayload:
    kind: PayloadKind
    value: str
    raw: str


def parse_start_payload(raw: str | None) -> StartPayload | None:
    """None — обычный /start без параметра. Нераспознанный параметр — UNKNOWN (сохраняется как есть для аналитики)."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()[:MAX_PAYLOAD_LENGTH]
    prefix, separator, value = raw.partition("_")
    kind = _PREFIXES.get(prefix)
    if separator and kind is not None and _VALUE.match(value):
        return StartPayload(kind=kind, value=value, raw=raw)
    return StartPayload(kind=PayloadKind.UNKNOWN, value=raw, raw=raw)
