import re
from dataclasses import dataclass

_LETTERS = "A-Za-zА-Яа-яЁё"
_NAME_WORD = re.compile(rf"^[{_LETTERS}]+(?:['\-][{_LETTERS}]+)*$")
MAX_NAME_PART_LENGTH = 100


@dataclass(frozen=True)
class FullName:
    last_name: str
    first_name: str
    middle_name: str | None


def parse_full_name(raw: str) -> FullName | None:
    """ФИО одной строкой: «Фамилия Имя [Отчество]». Регистр не меняем — как написал человек, так и в сертификат.

    Третье и следующие слова — отчество целиком («Мамедов Али Гусейн оглы» → отчество «Гусейн оглы»).
    None — меньше двух слов, есть цифры/эмодзи или часть длиннее лимита колонки.
    """
    words = raw.split()
    if len(words) < 2 or not all(_NAME_WORD.match(word) for word in words):
        return None
    last_name, first_name, *rest = words
    middle_name = " ".join(rest) or None
    if any(len(part) > MAX_NAME_PART_LENGTH for part in (last_name, first_name, middle_name or "")):
        return None
    return FullName(last_name, first_name, middle_name)


def full_name(last_name: str | None, first_name: str | None, middle_name: str | None) -> str:
    return " ".join(part for part in (last_name, first_name, middle_name) if part)
