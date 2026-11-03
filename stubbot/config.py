import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL


class OnlinePayments(StrEnum):
    """Онлайн-оплата заказов (полная версия, этап 1, шаг 7). Проверка настроек - payments.create_gateway."""

    OFF = "off"  # кнопки «Оплатить онлайн» нет: оплату отмечает админ
    FAKE = "fake"  # без ЮKassa: бот сам изображает оплату кнопками - только для проверки на локальной базе
    YOOKASSA = "yookassa"  # ЮKassa; тестовый магазин или боевой - по ключам YOOKASSA_*


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram. Токен не обязателен для alembic и скриптов, проверяется при старте бота.
    bot_token: SecretStr | None = None
    # Пусто - напрямую (локальная разработка). На сервере: socks5://127.0.0.1:1080 (sing-box).
    # SecretStr: URL прокси может содержать логин/пароль, в логи не попадает.
    telegram_proxy_url: SecretStr | None = None
    telegram_timeout: float = 30.0
    telegram_retry_attempts: int = 3

    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str
    db_password: SecretStr
    db_name: str
    # Своя схема в базе (search_path): так разработка веб-приложения живёт в той же базе, не трогая её таблицы
    # в public. Пусто - схема по умолчанию (public), как на проде.
    db_schema: str | None = None

    redis_url: str = "redis://127.0.0.1:6379/0"

    owner_telegram_id: int
    # Кто видит «⚙️ Админка» и может в неё войти: owner + эти id. В .env через запятую: ADMIN_IDS=111,222
    admin_ids: Annotated[list[int], NoDecode] = []

    log_level: str = "INFO"
    log_dir: Path = Path("logs")
    timezone: str = "Europe/Moscow"

    # Защита от флуда: не больше flood_limit обращений от одного человека за flood_window секунд. 0 - выключить.
    flood_limit: int = 20
    flood_window: float = 5.0

    # MVP - афиша: заявки, «Мои заявки» и «Сообщить о наборе» выключены (код в tg/handlers/application.py).
    applications_enabled: bool = False

    # Чат менеджера для записи: https://t.me/<username>. Пусто - кнопки «Менеджер» нет.
    # Короткие записи «t.me/<username>» и «@<username>» тоже принимаются - приводятся к полной ссылке.
    manager_url: str | None = None

    # Онлайн-оплата. Ключи магазина ЮKassa вписываются прямо в настройки сервера, в чат и в репозиторий - никогда.
    online_payments: OnlinePayments = OnlinePayments.OFF
    yookassa_shop_id: str | None = None
    yookassa_secret_key: SecretStr | None = None
    # Чек 54-ФЗ в запросе платежа (касса подключена в ЮKassa: «Чеки от ЮKassa» или касса партнёра). Значения даёт
    # бухгалтер заказчика: ставка НДС (vat_code 1-12), система налогообложения (1-6, нужна, если их несколько),
    # признак способа расчёта (весь остаток до курса - full_prepayment) и предмета расчёта (курс - service).
    yookassa_receipts: bool = False
    yookassa_vat_code: int | None = None
    yookassa_tax_system_code: int | None = None
    yookassa_payment_mode: str = "full_prepayment"
    yookassa_payment_subject: str = "service"

    # Веб-приложение (Telegram Mini App) - stubbot/web. Сервер API и страниц приложения поднимается рядом с ботом.
    webapp_enabled: bool = False
    webapp_host: str = "127.0.0.1"
    webapp_port: int = 8080
    # Публичный https-адрес приложения: по нему открывает Telegram (кнопка «Открыть приложение» в боте).
    # Пусто - кнопки в боте нет; приложение доступно только локально (разработка в браузере).
    webapp_url: str | None = None
    # Всё - в приложении: бот после /start присылает одно приветствие с кнопкой приложения, нижнего меню и кнопочных
    # разделов нет, кнопки уведомлений открывают нужный экран приложения. Код разделов бота остаётся: false - как было.
    webapp_only: bool = False
    # Вход без Telegram (заголовок «Authorization: dev <telegram_id>») - только для разработки в браузере
    # и только с локальной базой (webapp_dev_allowed).
    webapp_dev_auth: bool = False

    @property
    def online_payments_enabled(self) -> bool:
        return self.online_payments is not OnlinePayments.OFF

    @property
    def webapp_dev_allowed(self) -> bool:
        return self.webapp_dev_auth and self.db_host in ("localhost", "127.0.0.1", "::1")

    @field_validator("db_schema", "webapp_url", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("db_schema")
    @classmethod
    def _check_schema(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value):
            raise ValueError("DB_SCHEMA - латиница в нижнем регистре, цифры и _ (например webdev)")
        return value

    @field_validator("webapp_url")
    @classmethod
    def _check_webapp_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("WEBAPP_URL - только https://… (Telegram открывает приложения лишь по https)")
        return value.rstrip("/") if value else value

    @model_validator(mode="after")
    def _check_webapp_only(self) -> "Settings":
        if self.webapp_only and not self.webapp_url:
            raise ValueError("WEBAPP_ONLY=true - нужен WEBAPP_URL: без адреса приложения бот не сможет его открыть")
        return self

    @field_validator("yookassa_shop_id", "yookassa_secret_key", "yookassa_vat_code", "yookassa_tax_system_code",
                     mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        # В образце .env эти строки стоят пустыми, пока заказчик не дал значения.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(" ", "").split(",") if part]
        return value

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id == self.owner_telegram_id or telegram_id in self.admin_ids

    @property
    def admin_chat_ids(self) -> list[int]:
        """Кому уведомления о записях и заказах: владелец и все из ADMIN_IDS, без повторов."""
        return list(dict.fromkeys([self.owner_telegram_id, *self.admin_ids]))

    @field_validator("manager_url", mode="before")
    @classmethod
    def _check_manager_url(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip() or None
        if isinstance(value, str):
            # Ссылку вписывают руками, и чаще всего - так, как её видят в Telegram: «t.me/name» или «@name».
            if value.startswith("@"):
                value = f"https://t.me/{value[1:]}"
            elif value.startswith(("t.me/", "telegram.me/")):
                value = f"https://{value}"
            elif value.startswith(("http://t.me/", "http://telegram.me/")):
                value = "https://" + value.removeprefix("http://")
        if value is not None and not str(value).startswith(("https://", "tg://")):
            raise ValueError("MANAGER_URL - ссылка вида https://t.me/<username>")
        return value

    @property
    def database_url(self) -> URL:
        # URL.create экранирует спецсимволы в пароле; в строковом виде пароль не светится.
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
