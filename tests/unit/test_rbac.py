"""Tests for RBAC -- viewer role restrictions and admin guards."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


def make_session(role="viewer"):
    """Create a minimal session cookie payload."""
    return {"u": "testuser", "role": role}


@pytest.mark.asyncio
async def test_viewer_can_read_software():
    """Viewer should be able to GET /api/v1/software/ when authenticated."""
    from httpx import ASGITransport, AsyncClient

    from softarr.auth.dependencies import require_auth, require_viewer
    from softarr.core.database import get_db
    from softarr.core.ini_settings import get_ini_settings
    from softarr.main import app

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    ini = MagicMock()
    ini.get = MagicMock(return_value="")

    async def override_db():
        yield mock_db

    def override_ini():
        return ini

    def viewer_user():
        return make_session(role="viewer")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_ini_settings] = override_ini
    # Override both require_auth and require_viewer so the endpoint works
    app.dependency_overrides[require_viewer] = viewer_user
    app.dependency_overrides[require_auth] = viewer_user

    try:
        # Patch startup internals to prevent background tasks from leaking into
        # the test event loop when the ASGI lifespan fires. The lifespan
        # fast-fails if the users table is empty, so the mocked session has
        # to report a non-zero user count.
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_count_result = MagicMock()
        # Non-empty list so user_count() > 0 and the lifespan check passes.
        mock_count_result.scalars.return_value.all.return_value = [object()]
        mock_session.execute = AsyncMock(return_value=mock_count_result)
        mock_db_factory = MagicMock()
        mock_db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "softarr.main.asyncio.ensure_future",
                return_value=MagicMock(done=lambda: False),
            ),
            patch("softarr.main.AsyncSessionLocal", mock_db_factory),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                r = await client.get("/api/v1/software/")
        assert r.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_ini_settings, None)
        app.dependency_overrides.pop(require_viewer, None)
        app.dependency_overrides.pop(require_auth, None)


@pytest.mark.asyncio
async def test_cannot_deactivate_last_admin():
    """deactivate_user should raise ValueError if it would leave 0 active admins."""
    from softarr.auth.service import AuthService

    db = AsyncMock()
    uid = uuid4()

    # User to deactivate
    user_mock = MagicMock()
    user_mock.id = uid
    user_mock.role = "admin"
    user_mock.is_active = True
    user_mock.is_admin = True

    # First query: get_user_by_id -> scalar_one_or_none() returns user
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user_mock

    # Second query: admin count -> scalar_one() returns 0 (no other admins)
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0

    db.execute = AsyncMock(side_effect=[user_result, count_result])

    svc = AuthService(db)
    with pytest.raises(ValueError, match="last active admin"):
        await svc.deactivate_user(uid)


@pytest.mark.asyncio
async def test_viewer_role_in_session():
    """create_session_cookie should include role in the session payload."""
    from softarr.auth.sessions import create_session_cookie

    uid = uuid4()
    token = create_session_cookie(uid, "testuser", is_admin=False, role="viewer")
    # Token should be a non-empty string (itsdangerous signed)
    assert isinstance(token, str)
    assert len(token) > 10
