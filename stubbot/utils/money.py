NBSP = " "


def format_rub(kopecks: int) -> str:
    """4500000 → «45 000 ₽» (неразрывные пробелы, копейки только если есть)."""
    rubles, rest = divmod(kopecks, 100)
    text = f"{rubles:,}".replace(",", NBSP)
    if rest:
        text += f",{rest:02d}"
    return f"{text}{NBSP}₽"
