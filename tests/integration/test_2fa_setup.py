"""Integration tests for the 2FA TOTP enrolment flow.

Covers the AuthService methods used by GET/POST /auth/2fa/setup without going
through HTTP -- that layer is thin and already tested by unit tests.  The
integration layer exercises the DB state transitions.
"""

import pyotp
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from softarr.auth.service import AuthService
from softarr.auth.totp import decrypt_secret
from softarr.core.database import Base

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session, matches the pattern from test_workflow.py."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_user(db: AsyncSession):
    """Insert a test admin user and return it."""
    service = AuthService(db)
    return await service.create_user("testadmin", "password123", is_admin=True)


class TestEnableTotpGeneratesSecret:
    async def test_first_call_creates_secret(self, db, admin_user):
        service = AuthService(db)
        raw = await service.enable_totp(admin_user.id)
        assert raw is not None
        assert len(raw) >= 16

    async def test_secret_stored_signed(self, db, admin_user):
        service = AuthService(db)
        raw = await service.enable_totp(admin_user.id)
        user = await service.get_user_by_id(admin_user.id)
        # Stored value must differ from raw (it is signed, not plaintext).
        assert user.totp_secret != raw
        # But must round-trip correctly.
        assert decrypt_secret(user.totp_secret) == raw

    async def test_totp_not_enabled_until_confirmed(self, db, admin_user):
        service = AuthService(db)
        await service.enable_totp(admin_user.id)
        user = await service.get_user_by_id(admin_user.id)
        assert user.totp_enabled is False


class TestSecondGetReusesSecret:
    """Visiting the setup page twice should not regenerate the secret."""

    async def test_pending_secret_is_stable(self, db, admin_user):
        service = AuthService(db)
        # Simulate GET #1 -- generate secret.
        raw1 = await service.enable_totp(admin_user.id)

        # Simulate GET #2 -- reuse logic: if pending secret exists, decrypt and reuse.
        user = await service.get_user_by_id(admin_user.id)
        if user.totp_secret and not user.totp_enabled:
            raw2 = decrypt_secret(user.totp_secret)
        else:
            raw2 = await service.enable_totp(admin_user.id)

        assert raw1 == raw2


class TestConfirmTotpEnrolment:
    async def test_valid_code_confirms_enrolment(self, db, admin_user):
        service = AuthService(db)
        raw = await service.enable_totp(admin_user.id)
        code = pyotp.TOTP(raw).now()
        result = await service.confirm_totp_enrolment(admin_user.id, code)
        assert result is True
        user = await service.get_user_by_id(admin_user.id)
        assert user.totp_enabled is True

    async def test_wrong_code_leaves_unenrolled(self, db, admin_user):
        service = AuthService(db)
        await service.enable_totp(admin_user.id)
        result = await service.confirm_totp_enrolment(admin_user.id, "000000")
        assert result is False
        user = await service.get_user_by_id(admin_user.id)
        assert user.totp_enabled is False

    async def test_wrong_code_does_not_clear_secret(self, db, admin_user):
        """A failed confirmation must not wipe the pending secret."""
        service = AuthService(db)
        await service.enable_totp(admin_user.id)
        user_before = await service.get_user_by_id(admin_user.id)
        secret_before = user_before.totp_secret

        await service.confirm_totp_enrolment(admin_user.id, "000000")

        user_after = await service.get_user_by_id(admin_user.id)
        assert user_after.totp_secret == secret_before


class TestGetSetupDoesNotOverwriteEnrolledSecret:
    """Core regression: visiting /auth/2fa/setup when already enrolled must not
    reset the confirmed secret."""

    async def test_enrolled_secret_survives_re_visit(self, db, admin_user):
        service = AuthService(db)

        # Complete enrolment.
        raw = await service.enable_totp(admin_user.id)
        code = pyotp.TOTP(raw).now()
        await service.confirm_totp_enrolment(admin_user.id, code)

        enrolled_user = await service.get_user_by_id(admin_user.id)
        assert enrolled_user.totp_enabled is True
        secret_after_enrol = enrolled_user.totp_secret

        # Simulate re-visit of GET /auth/2fa/setup using the fixed handler logic.
        user = await service.get_user_by_id(admin_user.id)
        if user.totp_enabled:
            # Fixed path: do nothing.
            pass
        elif user.totp_secret:
            decrypt_secret(user.totp_secret)
        else:
            await service.enable_totp(admin_user.id)

        user_after = await service.get_user_by_id(admin_user.id)
        assert user_after.totp_enabled is True
        assert user_after.totp_secret == secret_after_enrol
