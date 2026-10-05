"""CSV для Excel: UTF-8 с BOM, разделитель «;» (так его открывает русский Excel), защита от формул в ячейках."""

import csv
import io
import re
from collections.abc import Iterable, Sequence

# Excel выполняет как формулу значение, которое начинается с одного из этих символов.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"[+-]?\d+")


def safe_cell(value: object) -> str:
    """Текст ячейки. Значения, похожие на формулу, получают апостроф в начале — Excel покажет их как текст.

    Числа со знаком (телефон +7…) формулой быть не могут и остаются как есть.
    """
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_START) and not _NUMBER.fullmatch(text):
        return "'" + text
    return text


def build_csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    return buffer.getvalue().encode("utf-8-sig")
