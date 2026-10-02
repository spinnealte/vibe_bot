from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram. Токен не обязателен для alembic и скриптов, проверяется при старте бота.
    bot_token: SecretStr | None = None
    # Пусто — напрямую (локальная разработка). На сервере: socks5://127.0.0.1:1080 (sing-box).
    # SecretStr: URL прокси может содержать логин/пароль, в логи не попадает.
    telegram_proxy_url: SecretStr | None = None
    telegram_timeout: float = 30.0
    telegram_retry_attempts: int = 3

    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str
    db_password: SecretStr
    db_name: str

    redis_url: str = "redis://127.0.0.1:6379/0"

    owner_telegram_id: int
    # Кто видит «⚙️ Админка» и может в неё войти: owner + эти id. В .env через запятую: ADMIN_IDS=111,222
    admin_ids: Annotated[list[int], NoDecode] = []

    log_level: str = "INFO"
    log_dir: Path = Path("logs")
    legal_docs_dir: Path = Path("legal")
    timezone: str = "Europe/Moscow"

    # MVP — афиша: заявки, «Мои заявки» и «Сообщить о наборе» выключены (код в tg/handlers/application.py).
    applications_enabled: bool = False

    # Чат менеджера для записи: https://t.me/<username>. Пусто — кнопки «Менеджер» нет.
    manager_url: str | None = None

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.replace(" ", "").split(",") if part]
        return value

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id == self.owner_telegram_id or telegram_id in self.admin_ids

    @field_validator("manager_url", mode="before")
    @classmethod
    def _check_manager_url(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip() or None
        if value is not None and not str(value).startswith(("https://", "tg://")):
            raise ValueError("MANAGER_URL — ссылка вида https://t.me/<username>")
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
