import re

_LETTERS = "A-Za-zА-Яа-яЁё"
_NAME_WORD = re.compile(rf"^[{_LETTERS}]+(?:['\-][{_LETTERS}]+)*$")
MAX_FULL_NAME_LENGTH = 300  # длина колонки clients.full_name / lecturers.full_name


def parse_full_name(raw: str) -> str | None:
    """ФИО одной строкой: «Фамилия Имя [Отчество…]». Регистр не меняем — как написал человек, так и в сертификат;
    лишние пробелы схлопываем.

    None — меньше двух слов, есть цифры/эмодзи или строка длиннее колонки.
    """
    words = raw.split()
    if len(words) < 2 or not all(_NAME_WORD.match(word) for word in words):
        return None
    name = " ".join(words)
    return name if len(name) <= MAX_FULL_NAME_LENGTH else None


def short_name(full_name: str) -> str:
    """«Иванов Сергей Петрович» → «Иванов С. П.»: первое слово — фамилия, остальные — инициалами."""
    last_name, *rest = full_name.split()
    return " ".join([last_name, *(f"{word[0]}." for word in rest)])
