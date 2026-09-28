from html import escape


def safe(value: object | None, placeholder: str = "—") -> str:
    """Пользовательские данные в HTML-сообщение — только через эту функцию."""
    if value is None or value == "":
        return placeholder
    return escape(str(value), quote=False)


def safe_join(values: list[str], placeholder: str = "—") -> str:
    return escape(", ".join(values), quote=False) if values else placeholder
