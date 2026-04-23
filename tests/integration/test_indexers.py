"""Integration tests for multi-indexer support.

Verifies that the indexer INI settings integrate correctly with the
release service and respect the global usenet_adapter_enabled switch.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from softarr.core.database import Base
from softarr.core.ini_settings import IniSettingsManager
from softarr.models.hash_intelligence import (
    HashIntelligence,  # noqa: F401 -- needed for create_all
)
from softarr.models.password_history import (
    PasswordHistory,  # noqa: F401 -- needed for create_all
)
from softarr.schemas.usenet_indexer import UsenetIndexerCreate
from softarr.services.usenet_indexer_service import UsenetIndexerService

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def ini(tmp_path):
    return IniSettingsManager(tmp_path / "softarr.ini")


class TestGetUsenetAdapter:
    """Test that _get_usenet_adapter() integrates with the INI indexer config."""

    @pytest.mark.asyncio
    async def test_adapter_loads_indexers_from_ini(self, db: AsyncSession, ini):
        from softarr.services.release_service import ReleaseService

        # Enable the adapter and create two indexers
        ini.set("usenet_adapter_enabled", "true")
        ini.create_indexer("Indexer A", "http://a.test", "key-a", priority=0)
        ini.create_indexer("Indexer B", "http://b.test", "key-b", priority=1)

        service = ReleaseService(db, ini)
        adapter = service._get_usenet_adapter()

        assert len(adapter.indexers) == 2
        assert adapter.indexers[0].name == "Indexer A"
        assert adapter.indexers[1].name == "Indexer B"
        # Keys should be unmasked (internal use)
        assert adapter.indexers[0].api_key == "key-a"

    @pytest.mark.asyncio
    async def test_master_switch_disabled_returns_empty(self, db: AsyncSession, ini):
        from softarr.services.release_service import ReleaseService

        # Master switch off, but indexers exist
        ini.set("usenet_adapter_enabled", "false")
        ini.create_indexer("Ignored", "http://x.test", "key-x")

        service = ReleaseService(db, ini)
        adapter = service._get_usenet_adapter()
        assert adapter.indexers == []

    @pytest.mark.asyncio
    async def test_no_indexers_returns_empty_adapter(self, db: AsyncSession, ini):
        from softarr.services.release_service import ReleaseService

        ini.set("usenet_adapter_enabled", "true")

        service = ReleaseService(db, ini)
        adapter = service._get_usenet_adapter()
        assert adapter.indexers == []

    @pytest.mark.asyncio
    async def test_disabled_indexers_excluded(self, db: AsyncSession, ini):
        from softarr.services.release_service import ReleaseService

        ini.set("usenet_adapter_enabled", "true")
        ini.create_indexer("Active", "http://a.test", "key-a", enabled=True)
        ini.create_indexer("Inactive", "http://b.test", "key-b", enabled=False)

        service = ReleaseService(db, ini)
        adapter = service._get_usenet_adapter()
        assert len(adapter.indexers) == 1
        assert adapter.indexers[0].name == "Active"


class TestIndexerCRUDLifecycle:
    """Full create -> read -> update -> delete cycle via the service."""

    def test_full_lifecycle(self, ini):
        service = UsenetIndexerService(ini)

        # Create
        created = service.create(
            UsenetIndexerCreate(
                name="Lifecycle", url="http://lc.test", api_key="lifecycle-key-123"
            )
        )
        assert created.name == "Lifecycle"
        assert created.api_key.startswith("****")

        # Read
        fetched = service.get_by_name("Lifecycle")
        assert fetched is not None
        assert fetched.name == created.name

        # List
        all_indexers = service.get_all()
        assert len(all_indexers) == 1

        # Update
        from softarr.schemas.usenet_indexer import UsenetIndexerUpdate

        updated = service.update(
            "Lifecycle", UsenetIndexerUpdate(name="Updated", priority=10)
        )
        assert updated.name == "Updated"
        assert updated.priority == 10

        # Delete
        assert service.delete("Updated") is True
        assert service.get_by_name("Updated") is None
        assert service.get_all() == []
