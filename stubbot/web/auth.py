"""Кто открыл приложение: подпись Telegram (initData) - пароля и своих аккаунтов у приложения нет.

Telegram передаёт приложению строку initData с данными пользователя и подписью HMAC от токена бота. Сервер
проверяет подпись (подделать её без токена нельзя) и свежесть (auth_date, не старше MAX_AGE). Номер телефона -
так же: ответ WebApp.requestContact подписан тем же способом, и номер в нём - самого пользователя.

Для разработки в обычном браузере (без Telegram) - «Authorization: dev <telegram_id>», только при
WEBAPP_DEV_AUTH=true и локальной базе (Settings.webapp_dev_allowed).
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

from aiogram.utils.web_app import check_webapp_signature

MAX_AGE = timedelta(hours=24)  # initData старше суток не принимаем - пусть Telegram откроет приложение заново
MAX_FUTURE = timedelta(minutes=5)  # часы сервера и Telegram могут немного расходиться


class AuthError(Exception):
    """Подпись не сошлась, данные устарели или их нет."""


@dataclass(frozen=True)
class WebUser:
    """Пользователь Telegram, открывший приложение."""

    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    start_param: str | None = None  # ссылка t.me/<bot>/<app>?startapp=… - как хвост /start у бота
    dev: bool = False  # вход разработчика без Telegram (не подпись initData)


def _signed_fields(data: str, bot_token: str, now: datetime, max_age: timedelta) -> dict[str, str]:
    if not data or not check_webapp_signature(bot_token, data):
        raise AuthError("подпись Telegram не сошлась")
    fields = dict(parse_qsl(data, keep_blank_values=True))
    try:
        auth_date = datetime.fromtimestamp(int(fields["auth_date"]), UTC)
    except (KeyError, ValueError, OverflowError) as error:
        raise AuthError("нет даты подписи") from error
    if not now - max_age <= auth_date <= now + MAX_FUTURE:
        raise AuthError("данные Telegram устарели")
    return fields


def user_from_init_data(init_data: str, bot_token: str, now: datetime | None = None,
                        max_age: timedelta = MAX_AGE) -> WebUser:
    fields = _signed_fields(init_data, bot_token, now or datetime.now(UTC), max_age)
    try:
        user = json.loads(fields["user"])
        return WebUser(id=int(user["id"]), first_name=user.get("first_name"), last_name=user.get("last_name"),
                       username=user.get("username"), language_code=user.get("language_code"),
                       start_param=fields.get("start_param") or None)
    except (KeyError, ValueError, TypeError) as error:
        raise AuthError("нет пользователя в данных Telegram") from error


def phone_from_contact(response: str, bot_token: str, user_id: int, now: datetime | None = None) -> str:
    """Номер из подписанного ответа WebApp.requestContact. Только свой: user_id в контакте - тот, кто вошёл."""
    fields = _signed_fields(response, bot_token, now or datetime.now(UTC), MAX_AGE)
    try:
        contact = json.loads(fields["contact"])
        phone, owner = str(contact["phone_number"]), int(contact["user_id"])
    except (KeyError, ValueError, TypeError) as error:
        raise AuthError("нет контакта в ответе Telegram") from error
    if owner != user_id:
        raise AuthError("чужой контакт")
    return phone
