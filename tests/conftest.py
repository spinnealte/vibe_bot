"""Общее для всех тестов: кнопочный бот (WEBAPP_ONLY=false), что бы ни стояло в .env. Клавиатуры и уведомления
админам читают настройки через get_settings() - без этого режим «всё в приложении» из .env менял бы их в тестах.
Сам этот режим проверяют test_app_only.py и tests/e2e/test_e2e_app_only.py."""

import pytest

from stubbot.config import get_settings


@pytest.fixture(autouse=True)
def _buttons_bot(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WEBAPP_ONLY", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
