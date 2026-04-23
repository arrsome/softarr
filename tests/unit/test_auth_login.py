"""Tests for the POST /auth/login HTML form handler.

Covers the regression where invalid credentials returned a raw JSON body
(`{"detail": "Invalid credentials"}`) instead of re-rendering login.html
with an error message in the existing `{% if error %}` block.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient


def _patched_lifespan():
    """Stub the startup lifespan so it doesn't touch a real DB or schedule jobs.

    The lifespan fast-fails if the users table is empty; it also schedules
    background tasks that would leak into the test event loop. Returns a
    context manager that patches both.
    """
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_count_result = MagicMock()
    # Non-empty list so user_count() > 0 and the lifespan check passes.
    mock_count_result.scalars.return_value.all.return_value = [object()]
    mock_session.execute = AsyncMock(return_value=mock_count_result)

    mock_db_factory = MagicMock()
    mock_db_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    return (
        patch(
            "softarr.main.asyncio.ensure_future",
            return_value=MagicMock(done=lambda: False),
        ),
        patch("softarr.main.AsyncSessionLocal", mock_db_factory),
    )


async def _post_login(form: dict) -> tuple[int, str, str]:
    """Send a form POST to /auth/login and return (status, content_type, body)."""
    from softarr.core.database import get_db
    from softarr.main import app

    async def override_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = override_db
    try:
        ensure_future_patch, session_patch = _patched_lifespan()
        with ensure_future_patch, session_patch:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/auth/login", data=form, follow_redirects=False
                )
        return (
            response.status_code,
            response.headers.get("content-type", ""),
            response.text,
        )
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_blank_credentials_renders_login_form_with_error():
    """Submitting an empty form re-renders login.html with a 400 + HTML body."""
    status, content_type, body = await _post_login({"username": "", "password": ""})

    assert status == 400
    assert "text/html" in content_type
    # The login form is the page we should be looking at.
    assert '<form action="/auth/login"' in body
    # And the error message lives in the existing {% if error %} block.
    assert "Username and password are required." in body
    # No raw JSON body should ever be served from the HTML login form.
    assert '{"detail"' not in body


async def test_invalid_credentials_renders_login_form_with_error():
    """Wrong password returns 401 + the login form, not a JSON detail body."""
    with patch(
        "softarr.auth.routes.AuthService.authenticate",
        new=AsyncMock(return_value=None),
    ):
        status, content_type, body = await _post_login(
            {"username": "admin", "password": "wrong"}
        )

    assert status == 401
    assert "text/html" in content_type
    assert '<form action="/auth/login"' in body
    assert "Invalid username or password." in body
    assert '{"detail"' not in body


async def test_valid_credentials_redirects():
    """Happy path: valid credentials still issue a 303 redirect (regression guard)."""
    from uuid import uuid4

    user = MagicMock()
    user.id = uuid4()
    user.username = "admin"
    user.is_admin = True
    user.role = "admin"
    user.force_password_change = False
    user.disclaimer_accepted = True
    user.totp_enabled = False

    with patch(
        "softarr.auth.routes.AuthService.authenticate",
        new=AsyncMock(return_value=user),
    ):
        status, _content_type, _body = await _post_login(
            {"username": "admin", "password": "correct"}
        )

    assert status == 303
