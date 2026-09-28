import re

_NON_DIGITS = re.compile(r"\D")


def normalize_phone(raw: str) -> str | None:
    """Номер в E.164 (+7XXXXXXXXXX). None — номер не распознан.

    Telegram присылает контакт как «79211234567» или «+79211234567»; российский «8…» приводим к «+7…».
    """
    digits = _NON_DIGITS.sub("", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    if 10 <= len(digits) <= 15:
        return "+" + digits
    return None
