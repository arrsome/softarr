import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from softarr.core.database import Base
from softarr.models.analysis import ReleaseAnalysis
from softarr.models.audit import AuditLog, ReleaseOverride
from softarr.models.release import Release
from softarr.models.software import Software

config = context.config
# Set the URL early so that alembic.ini %(DATABASE_URL)s interpolation succeeds.
# The value may be overridden again in run_migrations_online() -- that is fine.
config.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./dev/softarr.db"))
fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url():
    return os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./dev/softarr.db")


def run_migrations_offline():
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
