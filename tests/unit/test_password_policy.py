"""Unit tests for the PasswordPolicyService."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from softarr.services.password_policy_service import PasswordPolicyService


def _make_policy(**ini_values):
    """Create a PasswordPolicyService with mocked INI settings."""
    defaults = {
        "password_min_length": "12",
        "password_require_uppercase": "false",
        "password_require_numbers": "false",
        "password_require_special": "false",
        "password_history_count": "5",
        "password_max_age_days": "0",
    }
    defaults.update(ini_values)

    db = AsyncMock()
    ini = MagicMock()
    ini.get = MagicMock(side_effect=lambda k: defaults.get(k, ""))
    return PasswordPolicyService(db, ini)


class TestValidatePassword:
    def test_valid_password_returns_no_errors(self):
        policy = _make_policy()
        errors = policy.validate_password("correcthorsebatterystable")
        assert errors == []

    def test_too_short_returns_error(self):
        policy = _make_policy(password_min_length="16")
        errors = policy.validate_password("short")
        assert len(errors) == 1
        assert "16 characters" in errors[0]

    def test_min_length_exactly_met(self):
        policy = _make_policy(password_min_length="8")
        errors = policy.validate_password("exactlyy")
        assert errors == []

    def test_require_uppercase_fails(self):
        policy = _make_policy(password_require_uppercase="true")
        errors = policy.validate_password("nouppercase123")
        assert any("uppercase" in e.lower() for e in errors)

    def test_require_uppercase_passes(self):
        policy = _make_policy(password_require_uppercase="true")
        errors = policy.validate_password("HasUppercase123")
        assert not any("uppercase" in e.lower() for e in errors)

    def test_require_numbers_fails(self):
        policy = _make_policy(password_require_numbers="true")
        errors = policy.validate_password("nonumberhere!")
        assert any("number" in e.lower() for e in errors)

    def test_require_numbers_passes(self):
        policy = _make_policy(password_require_numbers="true")
        errors = policy.validate_password("hasanumber1here")
        assert not any("number" in e.lower() for e in errors)

    def test_require_special_fails(self):
        policy = _make_policy(password_require_special="true")
        errors = policy.validate_password("NoSpecialChars1")
        assert any("special" in e.lower() for e in errors)

    def test_require_special_passes(self):
        policy = _make_policy(password_require_special="true")
        errors = policy.validate_password("Has!SpecialChar1")
        assert not any("special" in e.lower() for e in errors)

    def test_multiple_rules_all_fail(self):
        policy = _make_policy(
            password_min_length="20",
            password_require_uppercase="true",
            password_require_numbers="true",
            password_require_special="true",
        )
        errors = policy.validate_password("short")
        assert len(errors) >= 3  # Too short + missing uppercase + number + special

    def test_all_rules_pass(self):
        policy = _make_policy(
            password_min_length="12",
            password_require_uppercase="true",
            password_require_numbers="true",
            password_require_special="true",
        )
        errors = policy.validate_password("C0mpl!xPassword99")
        assert errors == []


class TestCheckHistory:
    @pytest.mark.asyncio
    async def test_allows_when_not_in_history(self):
        from softarr.auth.passwords import hash_password

        policy = _make_policy(password_history_count="3")
        history_record = MagicMock()
        history_record.password_hash = hash_password("oldpassword123")

        async def fake_execute(_):
            result = AsyncMock()
            scalars_result = MagicMock()
            scalars_result.all = MagicMock(return_value=[history_record])
            result.scalars = MagicMock(return_value=scalars_result)
            return result

        policy.db.execute = fake_execute

        allowed = await policy.check_history(uuid4(), "newpassword456")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_blocks_when_password_in_history(self):
        from softarr.auth.passwords import hash_password

        the_password = "reusedpassword99"
        policy = _make_policy(password_history_count="5")
        history_record = MagicMock()
        history_record.password_hash = hash_password(the_password)

        async def fake_execute(_):
            result = AsyncMock()
            scalars_result = MagicMock()
            scalars_result.all = MagicMock(return_value=[history_record])
            result.scalars = MagicMock(return_value=scalars_result)
            return result

        policy.db.execute = fake_execute

        allowed = await policy.check_history(uuid4(), the_password)
        assert allowed is False

    @pytest.mark.asyncio
    async def test_allows_when_history_count_is_zero(self):
        policy = _make_policy(password_history_count="0")
        # Should not hit the database at all
        allowed = await policy.check_history(uuid4(), "anypassword")
        assert allowed is True
        policy.db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_history_always_allows(self):
        policy = _make_policy(password_history_count="5")

        async def fake_execute(_):
            result = AsyncMock()
            scalars_result = MagicMock()
            scalars_result.all = MagicMock(return_value=[])
            result.scalars = MagicMock(return_value=scalars_result)
            return result

        policy.db.execute = fake_execute

        allowed = await policy.check_history(uuid4(), "anypassword")
        assert allowed is True


class TestRoleBasedDependencies:
    """Tests for require_admin and require_viewer dependencies."""

    @pytest.mark.asyncio
    async def test_require_admin_blocks_viewer(self):
        from fastapi import HTTPException

        from softarr.auth.dependencies import require_admin

        # Build a mock request with a viewer session
        request = MagicMock()
        request.url.path = "/api/v1/some-endpoint"
        request.cookies.get = MagicMock(return_value=None)

        # No session -> 401
        with pytest.raises(HTTPException) as exc_info:
            await require_admin(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_require_auth_passes_admin(self):
        from uuid import uuid4

        from softarr.auth.dependencies import require_auth
        from softarr.auth.sessions import create_session_cookie, read_session_cookie

        token = create_session_cookie(uuid4(), "admin_user", is_admin=True)
        session_data = read_session_cookie(token)

        request = MagicMock()
        request.url.path = "/api/v1/something"
        request.cookies.get = MagicMock(return_value=token)

        user = await require_auth(request)
        assert user["role"] == "admin"
        assert user["u"] == "admin_user"

    @pytest.mark.asyncio
    async def test_require_auth_passes_viewer(self):
        from uuid import uuid4

        from softarr.auth.dependencies import require_auth
        from softarr.auth.sessions import create_session_cookie

        token = create_session_cookie(uuid4(), "viewer_user", is_admin=False)

        request = MagicMock()
        request.url.path = "/api/v1/something"
        request.cookies.get = MagicMock(return_value=token)

        user = await require_auth(request)
        assert user["role"] == "viewer"

    @pytest.mark.asyncio
    async def test_require_admin_passes_admin(self):
        from uuid import uuid4

        from softarr.auth.dependencies import require_admin
        from softarr.auth.sessions import create_session_cookie

        token = create_session_cookie(uuid4(), "admin_user", is_admin=True)

        request = MagicMock()
        request.url.path = "/api/v1/something"
        request.cookies.get = MagicMock(return_value=token)

        user = await require_admin(request)
        assert user["role"] == "admin"

    @pytest.mark.asyncio
    async def test_require_admin_blocks_viewer_role(self):
        from uuid import uuid4

        from fastapi import HTTPException

        from softarr.auth.dependencies import require_admin
        from softarr.auth.sessions import create_session_cookie

        token = create_session_cookie(uuid4(), "viewer_user", is_admin=False)

        request = MagicMock()
        request.url.path = "/api/v1/something"
        request.cookies.get = MagicMock(return_value=token)

        with pytest.raises(HTTPException) as exc_info:
            await require_admin(request)
        assert exc_info.value.status_code == 403

    def test_session_includes_role_and_fpc(self):
        from uuid import uuid4

        from softarr.auth.sessions import create_session_cookie, read_session_cookie

        uid = uuid4()
        token = create_session_cookie(
            uid, "alice", is_admin=True, force_password_change=True
        )
        data = read_session_cookie(token)

        assert data["role"] == "admin"
        assert data["fpc"] is True
        assert data["u"] == "alice"

    def test_viewer_session_role(self):
        from uuid import uuid4

        from softarr.auth.sessions import create_session_cookie, read_session_cookie

        token = create_session_cookie(uuid4(), "bob", is_admin=False)
        data = read_session_cookie(token)
        assert data["role"] == "viewer"
