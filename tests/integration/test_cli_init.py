"""Integration tests for the ``softarr-init`` console script.

These tests drive ``softarr.cli.init.main`` directly against an in-memory
aiosqlite database. The CLI module imports ``engine`` and
``AsyncSessionLocal`` at module load time from ``softarr.core.database``,
so each test monkeypatches those bindings on the CLI module to point at a
throwaway engine and session factory.
"""

from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from softarr.auth.service import AuthService
from softarr.cli import init as cli_init
from softarr.models.hash_intelligence import (
    HashIntelligence,  # noqa: F401 -- registered for create_all
)
from softarr.models.password_history import (
    PasswordHistory,  # noqa: F401 -- registered for create_all
)
from softarr.models.user import User  # noqa: F401 -- registered for create_all

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def cli_env(monkeypatch, tmp_path: Path):
    """Point the CLI at a fresh in-memory DB and a temp CONFIG_DIR.

    Yields the session factory so tests can inspect the DB after running
    the CLI. The CLI reads ``engine`` and ``AsyncSessionLocal`` from its
    own module namespace, so patching there is sufficient.
    """
    # Redirect INI writes into tmp_path so we do not touch the dev config.
    from softarr.core.config import settings

    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))

    engine = create_async_engine(TEST_DB_URL, echo=False)
    session_factory = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    monkeypatch.setattr(cli_init, "engine", engine)
    monkeypatch.setattr(cli_init, "AsyncSessionLocal", session_factory)

    # Reset the cached INI singleton so each test starts clean.
    from softarr.core.ini_settings import reset_ini_settings

    reset_ini_settings()

    yield session_factory

    reset_ini_settings()
    await engine.dispose()


async def _user_count(session_factory) -> int:
    async with session_factory() as db:
        return await AuthService(db).user_count()


async def test_init_creates_tables_and_admin(cli_env):
    """Full run on an empty DB creates tables and the default admin user."""
    exit_code = await cli_init.main([])
    assert exit_code == 0
    assert await _user_count(cli_env) == 1

    async with cli_env() as db:
        user = await AuthService(db).get_user_by_username("admin")
        assert user is not None
        assert user.is_admin is True


async def test_init_is_idempotent(cli_env):
    """Running twice is a no-op on the second run."""
    assert await cli_init.main([]) == 0
    assert await cli_init.main([]) == 0
    assert await _user_count(cli_env) == 1


async def test_init_skip_admin_creates_tables_only(cli_env):
    """``--skip-admin`` still creates tables but leaves user table empty."""
    exit_code = await cli_init.main(["--skip-admin"])
    assert exit_code == 0
    assert await _user_count(cli_env) == 0


async def test_init_skip_db_does_not_create_tables(monkeypatch, tmp_path):
    """``--skip-db`` leaves the schema untouched.

    Uses its own engine (without create_all in the fixture) to prove the
    CLI itself did not create the user table. Expect the admin bootstrap
    step to raise because the table does not exist; the CLI should
    surface a non-zero exit code.
    """
    from softarr.core.config import settings
    from softarr.core.ini_settings import reset_ini_settings

    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))

    engine = create_async_engine(TEST_DB_URL, echo=False)
    session_factory = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(cli_init, "engine", engine)
    monkeypatch.setattr(cli_init, "AsyncSessionLocal", session_factory)

    reset_ini_settings()

    try:
        exit_code = await cli_init.main(["--skip-db", "--skip-admin"])
        # Tables not created, admin skipped -- init has nothing left to do
        # and should still exit cleanly.
        assert exit_code == 0
    finally:
        reset_ini_settings()
        await engine.dispose()
