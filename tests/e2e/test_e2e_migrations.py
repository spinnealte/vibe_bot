"""Миграции Alembic на чистой схеме: каждая по очереди вверх, вниз и снова вверх («лесенка»), в конце схема
совпадает с моделями (alembic check). На проде схема меняется только миграциями - они должны откатываться."""

from alembic import command
from alembic.script import ScriptDirectory

from tests.e2e.conftest import ROOT, _connection_settings, drop_schema, new_schema, run_alembic


def test_migrations_stairway_and_models_match() -> None:
    settings = _connection_settings()  # только локальная база
    schema = new_schema(settings, prefix="mig")
    try:
        def stairway(config) -> None:
            script = ScriptDirectory.from_config(config)
            revisions = [rev.revision for rev in reversed(list(script.walk_revisions("base", "heads")))]
            assert revisions, "миграций нет"
            for revision in revisions:
                command.upgrade(config, revision)
                command.downgrade(config, "-1")
                command.upgrade(config, revision)
            command.check(config)  # модели и схема после всех миграций совпадают
            command.downgrade(config, "base")
            command.upgrade(config, "head")

        run_alembic(schema, stairway)
    finally:
        drop_schema(settings, schema)


def test_single_head() -> None:
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1
