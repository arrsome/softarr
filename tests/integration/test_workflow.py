"""Integration tests using an in-memory SQLite database.

These tests verify end-to-end flows against an in-memory SQLite backend.
Run with: mise run test.
"""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from softarr.core.database import Base
from softarr.core.ini_settings import IniSettingsManager
from softarr.models.analysis import ReleaseAnalysis
from softarr.models.audit import ReleaseOverride
from softarr.models.hash_intelligence import (
    HashIntelligence,  # noqa: F401 -- needed for create_all
)
from softarr.models.password_history import (
    PasswordHistory,  # noqa: F401 -- needed for create_all
)
from softarr.models.release import FlagStatus, Release, TrustStatus, WorkflowState
from softarr.models.software import Software

# ---------------------------------------------------------------------------
# Test database fixture
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db():
    """Create an in-memory database and yield a session."""
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
    """Create an IniSettingsManager backed by a temp file."""
    return IniSettingsManager(tmp_path / "softarr.ini")


@pytest_asyncio.fixture
async def sample_software(db: AsyncSession) -> Software:
    """Insert a sample software entry."""
    sw = Software(
        id=uuid4(),
        canonical_name="TestApp",
        aliases=["testapp", "ta"],
        expected_publisher="TestCorp",
        supported_os=["windows", "linux"],
        architecture="x64",
    )
    db.add(sw)
    await db.commit()
    await db.refresh(sw)
    return sw


@pytest_asyncio.fixture
async def sample_release(db: AsyncSession, sample_software: Software) -> Release:
    """Insert a sample release in DISCOVERED state."""
    rel = Release(
        id=uuid4(),
        software_id=sample_software.id,
        name="TestApp v1.0.0",
        version="1.0.0",
        source_type="github",
        source_origin="https://github.com/test/testapp/releases/tag/v1.0.0",
        workflow_state=WorkflowState.DISCOVERED,
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    return rel


# ---------------------------------------------------------------------------
# Software tests
# ---------------------------------------------------------------------------


class TestSoftwareCRUD:
    @pytest.mark.asyncio
    async def test_create_software(self, db: AsyncSession):
        from softarr.schemas.software import SoftwareCreate
        from softarr.services.software_service import SoftwareService

        service = SoftwareService(db)
        result = await service.create_software(
            SoftwareCreate(
                canonical_name="NewApp",
                expected_publisher="NewCorp",
                supported_os=["windows"],
            )
        )
        assert result.canonical_name == "NewApp"
        assert result.id is not None

    @pytest.mark.asyncio
    async def test_create_software_with_lists(self, db: AsyncSession):
        from softarr.schemas.software import SoftwareCreate
        from softarr.services.software_service import SoftwareService

        service = SoftwareService(db)
        result = await service.create_software(
            SoftwareCreate(
                canonical_name="LibreOffice",
                expected_publisher="The Document Foundation",
                aliases=["libreoffice", "LO"],
                supported_os=["windows", "linux", "macos"],
                architecture="x64",
                notes="Open-source office suite",
            )
        )
        assert result.canonical_name == "LibreOffice"
        assert result.aliases == ["libreoffice", "LO"]
        assert result.supported_os == ["windows", "linux", "macos"]
        assert result.notes == "Open-source office suite"

    @pytest.mark.asyncio
    async def test_create_software_with_defaults(self, db: AsyncSession):
        from softarr.schemas.software import SoftwareCreate
        from softarr.services.software_service import SoftwareService

        service = SoftwareService(db)
        result = await service.create_software(
            SoftwareCreate(
                canonical_name="MinimalApp",
            )
        )
        assert result.canonical_name == "MinimalApp"
        assert result.aliases == []
        assert result.supported_os == []
        assert result.expected_publisher is None
        assert result.architecture is None
        assert result.notes is None

    @pytest.mark.asyncio
    async def test_list_software(self, db: AsyncSession, sample_software):
        from softarr.services.software_service import SoftwareService

        service = SoftwareService(db)
        results = await service.get_all_software()
        assert len(results) >= 1
        assert any(r.canonical_name == "TestApp" for r in results)


# ---------------------------------------------------------------------------
# Workflow state transition tests
# ---------------------------------------------------------------------------


class TestWorkflowTransitions:
    @pytest.mark.asyncio
    async def test_discover_to_staged(
        self, db: AsyncSession, ini, sample_release: Release
    ):
        from softarr.services.release_service import ReleaseService

        service = ReleaseService(db, ini)
        updated = await service.transition_state(
            sample_release.id, WorkflowState.STAGED, changed_by="test"
        )
        assert updated.workflow_state == WorkflowState.STAGED
        assert updated.workflow_changed_by == "test"

    @pytest.mark.asyncio
    async def test_staged_to_approved(
        self, db: AsyncSession, ini, sample_release: Release
    ):
        from softarr.services.release_service import ReleaseService

        service = ReleaseService(db, ini)
        await service.transition_state(sample_release.id, WorkflowState.STAGED)
        await service.transition_state(sample_release.id, WorkflowState.UNDER_REVIEW)
        updated = await service.approve_release(sample_release.id, approved_by="admin")
        assert updated.workflow_state == WorkflowState.APPROVED
        assert updated.trust_status == TrustStatus.ADMIN_VERIFIED

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(
        self, db: AsyncSession, ini, sample_release: Release
    ):
        from softarr.services.release_service import ReleaseService

        service = ReleaseService(db, ini)
        with pytest.raises(ValueError, match="Cannot transition"):
            await service.transition_state(sample_release.id, WorkflowState.APPROVED)

    @pytest.mark.asyncio
    async def test_reject_and_restage(
        self, db: AsyncSession, ini, sample_release: Release
    ):
        from softarr.services.release_service import ReleaseService

        service = ReleaseService(db, ini)
        await service.transition_state(sample_release.id, WorkflowState.STAGED)
        await service.transition_state(sample_release.id, WorkflowState.REJECTED)
        updated = await service.transition_state(
            sample_release.id, WorkflowState.STAGED
        )
        assert updated.workflow_state == WorkflowState.STAGED


# ---------------------------------------------------------------------------
# Override tests
# ---------------------------------------------------------------------------


class TestOverride:
    @pytest.mark.asyncio
    async def test_override_creates_record(
        self, db: AsyncSession, ini, sample_release: Release
    ):
        from sqlalchemy import select

        from softarr.services.release_service import ReleaseService

        # Move to staged first so override (which approves) is valid
        service = ReleaseService(db, ini)
        await service.transition_state(sample_release.id, WorkflowState.STAGED)
        await service.transition_state(sample_release.id, WorkflowState.UNDER_REVIEW)

        updated = await service.override_release(
            sample_release.id, overridden_by="admin", reason="Testing override"
        )
        assert updated.trust_status == TrustStatus.ADMIN_VERIFIED
        assert updated.workflow_state == WorkflowState.APPROVED

        # Verify override record was persisted
        result = await db.execute(
            select(ReleaseOverride).where(
                ReleaseOverride.release_id == sample_release.id
            )
        )
        overrides = result.scalars().all()
        assert len(overrides) == 1
        assert overrides[0].override_reason == "Testing override"
        assert overrides[0].overridden_by == "admin"

    @pytest.mark.asyncio
    async def test_override_restricted_requires_reason(
        self, db: AsyncSession, ini, sample_release: Release
    ):
        from softarr.services.release_service import ReleaseService

        sample_release.flag_status = FlagStatus.RESTRICTED
        await db.commit()

        service = ReleaseService(db, ini)
        await service.transition_state(sample_release.id, WorkflowState.STAGED)
        await service.transition_state(sample_release.id, WorkflowState.UNDER_REVIEW)

        with pytest.raises(ValueError, match="reason is required"):
            await service.override_release(
                sample_release.id, overridden_by="admin", reason=None
            )


# ---------------------------------------------------------------------------
# Analysis persistence tests
# ---------------------------------------------------------------------------


class TestAnalysis:
    @pytest.mark.asyncio
    async def test_analysis_persists(self, db: AsyncSession, sample_release: Release):
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from softarr.services.analysis_service import AnalysisService

        # Load with software relationship
        result = await db.execute(
            select(Release)
            .options(selectinload(Release.software))
            .where(Release.id == sample_release.id)
        )
        release = result.scalar_one()

        service = AnalysisService(db)
        results = await service.analyze_release(release, raw_data={})

        assert "signature_status" in results
        assert "confidence_score" in results

        # Verify DB record
        result = await db.execute(
            select(ReleaseAnalysis).where(
                ReleaseAnalysis.release_id == sample_release.id
            )
        )
        analysis = result.scalar_one()
        assert analysis.signature_status is not None


# ---------------------------------------------------------------------------
# Settings persistence tests
# ---------------------------------------------------------------------------


class TestSettings:
    def test_set_and_get(self, ini):
        from softarr.services.settings_service import SettingsService

        service = SettingsService(ini)
        service.set("sabnzbd_url", "http://sab:8080")
        value = service.get("sabnzbd_url")
        assert value == "http://sab:8080"

    def test_secret_masking(self, ini):
        from softarr.services.settings_service import SettingsService

        service = SettingsService(ini)
        service.set("sabnzbd_api_key", "super-secret-key-12345")

        masked = service.get_all_masked()
        assert "sabnzbd_api_key" in masked
        assert masked["sabnzbd_api_key"].startswith("****")
        assert (
            "12345" not in masked["sabnzbd_api_key"]
            or masked["sabnzbd_api_key"] == "****2345"
        )
        assert masked["sabnzbd_api_key_is_set"] is True

    def test_update_existing(self, ini):
        from softarr.services.settings_service import SettingsService

        service = SettingsService(ini)
        service.set("sabnzbd_url", "http://old:8080")
        service.set("sabnzbd_url", "http://new:9090")
        value = service.get("sabnzbd_url")
        assert value == "http://new:9090"

    def test_default_value(self, ini):
        from softarr.services.settings_service import SettingsService

        service = SettingsService(ini)
        value = service.get("sabnzbd_category")
        assert value == "software"  # default from SETTING_DEFINITIONS


# ---------------------------------------------------------------------------
# Audit logging tests
# ---------------------------------------------------------------------------


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_log_and_retrieve(self, db: AsyncSession):
        from softarr.services.audit_service import AuditService

        service = AuditService(db)
        entry = await service.log_action(
            "test_action", "test_entity", uuid4(), user="tester", details={"key": "val"}
        )
        assert entry.action == "test_action"

        logs = await service.get_logs(limit=10)
        assert len(logs) >= 1
        assert logs[0].action == "test_action"


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class TestAuthService:
    @pytest.mark.asyncio
    async def test_create_and_authenticate(self, db: AsyncSession):
        from softarr.auth.service import AuthService

        service = AuthService(db)
        user = await service.create_user("testadmin", "testpass123", is_admin=True)
        assert user.username == "testadmin"

        authenticated = await service.authenticate("testadmin", "testpass123")
        assert authenticated is not None
        assert authenticated.username == "testadmin"

    @pytest.mark.asyncio
    async def test_wrong_password_fails(self, db: AsyncSession):
        from softarr.auth.service import AuthService

        service = AuthService(db)
        await service.create_user("user1", "correct")

        result = await service.authenticate("user1", "wrong")
        assert result is None

    @pytest.mark.asyncio
    async def test_bootstrap_creates_admin(self, db: AsyncSession):
        from softarr.auth.service import AuthService

        service = AuthService(db)
        password = await service.bootstrap_admin()
        assert password is not None  # Default password returned
        assert isinstance(password, str)
        assert len(password) >= 5  # Default is "admin" (5 chars)

        # Second call should not create another
        password2 = await service.bootstrap_admin()
        assert password2 is None

    @pytest.mark.asyncio
    async def test_change_password(self, db: AsyncSession):
        from softarr.auth.service import AuthService

        service = AuthService(db)
        user = await service.create_user("pwuser", "oldpass")
        await service.change_password(user.id, "newpass")

        assert await service.authenticate("pwuser", "oldpass") is None
        assert await service.authenticate("pwuser", "newpass") is not None


# ---------------------------------------------------------------------------
# Staging queue service tests
# ---------------------------------------------------------------------------


class TestStagingQueueService:
    @pytest.mark.asyncio
    async def test_get_staging_queue_returns_staged_releases(
        self, db: AsyncSession, sample_software: Software, ini: IniSettingsManager
    ):
        """get_staging_queue returns releases in STAGED and UNDER_REVIEW states."""
        from softarr.services.release_service import ReleaseService

        staged = Release(
            id=uuid4(),
            software_id=sample_software.id,
            name="StagedApp v1.0",
            version="1.0",
            source_type="github",
            workflow_state=WorkflowState.STAGED,
        )
        discovered = Release(
            id=uuid4(),
            software_id=sample_software.id,
            name="DiscoveredApp v2.0",
            version="2.0",
            source_type="github",
            workflow_state=WorkflowState.DISCOVERED,
        )
        db.add(staged)
        db.add(discovered)
        await db.commit()

        service = ReleaseService(db, ini)
        queue = await service.get_staging_queue()
        names = [r.name for r in queue]
        assert "StagedApp v1.0" in names
        assert "DiscoveredApp v2.0" not in names

    @pytest.mark.asyncio
    async def test_get_discovered_releases_returns_discovered_only(
        self, db: AsyncSession, sample_software: Software, ini: IniSettingsManager
    ):
        """get_discovered_releases returns only DISCOVERED-state releases."""
        from softarr.services.release_service import ReleaseService

        discovered = Release(
            id=uuid4(),
            software_id=sample_software.id,
            name="NewDiscovery v3.0",
            version="3.0",
            source_type="usenet",
            workflow_state=WorkflowState.DISCOVERED,
        )
        db.add(discovered)
        await db.commit()

        service = ReleaseService(db, ini)
        results = await service.get_discovered_releases()
        assert any(r.name == "NewDiscovery v3.0" for r in results)

    @pytest.mark.asyncio
    async def test_bulk_approve_transitions_staged_releases(
        self,
        db: AsyncSession,
        sample_software: Software,
        ini: IniSettingsManager,
    ):
        """Bulk approval transitions all specified STAGED releases to APPROVED."""
        from softarr.services.release_service import ReleaseService

        # Releases must be in UNDER_REVIEW state to be directly approvable
        rel1 = Release(
            id=uuid4(),
            software_id=sample_software.id,
            name="BulkApp v1.0",
            version="1.0",
            source_type="github",
            workflow_state=WorkflowState.UNDER_REVIEW,
        )
        rel2 = Release(
            id=uuid4(),
            software_id=sample_software.id,
            name="BulkApp v1.1",
            version="1.1",
            source_type="github",
            workflow_state=WorkflowState.UNDER_REVIEW,
        )
        db.add(rel1)
        db.add(rel2)
        await db.commit()
        await db.refresh(rel1)
        await db.refresh(rel2)

        service = ReleaseService(db, ini)
        result = await service.bulk_approve([rel1.id, rel2.id], user="test_user")

        assert len(result["succeeded"]) == 2
        assert len(result["failed"]) == 0
        await db.refresh(rel1)
        await db.refresh(rel2)
        assert rel1.workflow_state == WorkflowState.APPROVED
        assert rel2.workflow_state == WorkflowState.APPROVED
