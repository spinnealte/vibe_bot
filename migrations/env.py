import asyncio
import re
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from alembic.script import write_hooks
from sqlalchemy import Enum
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from stubbot.config import get_settings
from stubbot.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Enum(native_enum=False) в миграции — обычный VARCHAR; CHECK рендерится отдельно по naming convention."""
    if type_ == "type" and isinstance(obj, Enum) and not obj.native_enum:
        return f"sa.String(length={obj.length})"
    return False


# Alembic рендерит CHECK от Enum дважды: по конвенции (name=op.f('ck_...')) и под голым именем enum
# (name='admin_role'). Второй — дубль, при upgrade дал бы лишнее ограничение. Хук вычищает его из файла миграции.
_ENUM_CHECK_DUPLICATE = re.compile(r"^[ \t]*sa\.CheckConstraint\(.*, name='[^']*'\),?[ \t]*\r?\n", re.MULTILINE)


@write_hooks.register("drop_enum_check_duplicates")
def drop_enum_check_duplicates(filename: str, options: dict) -> None:
    path = Path(filename)
    path.write_text(_ENUM_CHECK_DUPLICATE.sub("", path.read_text(encoding="utf-8")), encoding="utf-8")


MIGRATION_OPTIONS = {
    "target_metadata": target_metadata,
    "compare_type": True,
    "render_item": render_item,
}


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url.render_as_string(hide_password=False),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **MIGRATION_OPTIONS,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, **MIGRATION_OPTIONS)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
