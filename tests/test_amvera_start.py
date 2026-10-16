"""Стартовый скрипт для Amvera: порядок шагов и «справочники - только на пустую базу». Без БД и Telegram."""

import importlib
import os

import pytest


@pytest.fixture
def starter(monkeypatch: pytest.MonkeyPatch):
    # Модуль при импорте задаёт LOG_DIR, если его нет.
    monkeypatch.delenv("LOG_DIR", raising=False)
    monkeypatch.setenv("START_DELAY", "0")
    import amvera_start

    yield importlib.reload(amvera_start)
    os.environ.pop("LOG_DIR", None)  # заданное модулем значение не должно достаться другим тестам


def _trace(starter, monkeypatch: pytest.MonkeyPatch, empty: bool) -> list[str]:
    steps: list[str] = []

    async def database_is_empty() -> bool:
        steps.append("проверка базы")
        return empty

    monkeypatch.setattr(starter.time, "sleep", lambda seconds: steps.append(f"пауза {seconds:g}"))
    monkeypatch.setattr(starter, "migrate", lambda: steps.append("миграции"))
    monkeypatch.setattr(starter, "database_is_empty", database_is_empty)
    monkeypatch.setattr(starter, "seed_database", lambda: steps.append("справочники"))
    monkeypatch.setattr(starter, "run_bot", lambda: steps.append("бот") or 0)
    assert starter.main() == 0
    return steps


def test_storage_paths_default_to_persistent_data(starter) -> None:
    assert os.environ["LOG_DIR"] == "/data/logs"  # не задано - в постоянное хранилище


def test_log_dir_from_project_settings_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_DIR", "/custom/logs")
    import amvera_start

    importlib.reload(amvera_start)
    assert os.environ["LOG_DIR"] == "/custom/logs"  # задано в переменных проекта - не трогаем


def test_fresh_database_gets_migrations_and_seed(starter, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _trace(starter, monkeypatch, empty=True) == ["пауза 0", "миграции", "проверка базы", "справочники", "бот"]


def test_existing_database_is_not_reseeded(starter, monkeypatch: pytest.MonkeyPatch) -> None:
    """Иначе каждый перезапуск возвращал бы специальности, которые админ переименовал или убрал."""
    assert _trace(starter, monkeypatch, empty=False) == ["пауза 0", "миграции", "проверка базы", "бот"]


def test_failed_migration_stops_start(starter, monkeypatch: pytest.MonkeyPatch) -> None:
    """Миграции не прошли - бота не запускаем: Amvera перезапустит контейнер, в логе будет причина."""
    started: list[str] = []

    def broken() -> None:
        raise RuntimeError("нет связи с базой")

    monkeypatch.setattr(starter.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(starter, "migrate", broken)
    monkeypatch.setattr(starter, "run_bot", lambda: started.append("бот") or 0)
    with pytest.raises(RuntimeError):
        starter.main()
    assert started == []
