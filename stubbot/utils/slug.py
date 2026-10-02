import re

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_NOT_SLUG = re.compile(r"[^a-z0-9]+")
MAX_SLUG_LENGTH = 50  # колонка programs.slug — 64, запас под суффикс «-2»


def slugify(title: str) -> str:
    """«Имплантация: от А до Я» → «implantatsiya-ot-a-do-ya». Пусто (одни символы) — «course»."""
    latin = "".join(_TRANSLIT.get(char, char) for char in title.lower())
    slug = _NOT_SLUG.sub("-", latin).strip("-")[:MAX_SLUG_LENGTH].rstrip("-")
    return slug or "course"
