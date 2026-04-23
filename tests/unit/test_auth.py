from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from softarr.auth.passwords import hash_password, verify_password


class TestPasswords:
    def test_hash_and_verify(self):
        pw = "test-password-123"
        hashed = hash_password(pw)
        assert hashed != pw
        assert hashed.startswith("$2")  # bcrypt prefix
        assert verify_password(pw, hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_empty_password_hash(self):
        hashed = hash_password("")
        assert verify_password("", hashed) is True
        assert verify_password("notempty", hashed) is False

    def test_verify_garbage_hash_returns_false(self):
        assert verify_password("test", "not-a-hash") is False

    def test_verify_none_hash_returns_false(self):
        assert verify_password("test", None) is False

    def test_different_salts_produce_different_hashes(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt uses random salt
        assert verify_password("same", h1) is True
        assert verify_password("same", h2) is True

    def test_default_password_verifies(self):
        """Sanity-check that the default 'admin' password round-trips correctly."""
        hashed = hash_password("admin")
        assert verify_password("admin", hashed) is True
        assert verify_password("wrong", hashed) is False


class TestBootstrapAdmin:
    @pytest.mark.asyncio
    async def test_returns_default_password_when_no_users(self):
        """bootstrap_admin returns the configured default password on a fresh DB."""
        from softarr.auth.service import AuthService

        db = AsyncMock()
        db.add = MagicMock()
        count_result = MagicMock()
        count_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=count_result)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        with patch("softarr.auth.service.settings") as mock_settings:
            mock_settings.ADMIN_PASSWORD_HASH = ""
            mock_settings.ADMIN_DEFAULT_PASSWORD = "admin"
            mock_settings.ADMIN_USERNAME = "admin"

            svc = AuthService(db)
            with patch.object(svc, "create_user", new=AsyncMock()) as mock_create:
                result = await svc.bootstrap_admin()

        assert result == "admin"
        mock_create.assert_called_once_with(
            username="admin", password="admin", is_admin=True
        )

    @pytest.mark.asyncio
    async def test_returns_none_when_users_exist(self):
        """bootstrap_admin does nothing and returns None when users already exist."""
        from softarr.auth.service import AuthService

        db = AsyncMock()
        count_result = MagicMock()
        count_result.scalars.return_value.all.return_value = [object()]
        db.execute = AsyncMock(return_value=count_result)

        svc = AuthService(db)
        result = await svc.bootstrap_admin()
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_prehashed_password_from_env(self):
        """bootstrap_admin uses ADMIN_PASSWORD_HASH when set, returning None."""
        from softarr.auth.service import AuthService

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        count_result = MagicMock()
        count_result.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=count_result)

        with patch("softarr.auth.service.settings") as mock_settings:
            mock_settings.ADMIN_PASSWORD_HASH = "$2b$12$somehash"
            mock_settings.ADMIN_USERNAME = "admin"

            svc = AuthService(db)
            result = await svc.bootstrap_admin()

        assert result is None
        db.add.assert_called_once()


# ---------------------------------------------------------------------------
# Disclaimer acceptance
# ---------------------------------------------------------------------------


class TestDisclaimerAcceptance:
    @pytest.mark.asyncio
    async def test_accept_disclaimer_sets_fields(self):
        """accept_disclaimer sets disclaimer_accepted=True and records timestamp."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from uuid import uuid4

        from softarr.auth.service import AuthService

        uid = uuid4()
        mock_user = MagicMock()
        mock_user.disclaimer_accepted = False
        mock_user.disclaimer_accepted_at = None

        db = AsyncMock()
        db.commit = AsyncMock()

        svc = AuthService(db)
        with patch.object(svc, "get_user_by_id", new=AsyncMock(return_value=mock_user)):
            result = await svc.accept_disclaimer(uid)

        assert result is True
        assert mock_user.disclaimer_accepted is True
        assert mock_user.disclaimer_accepted_at is not None
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_accept_disclaimer_returns_false_for_unknown_user(self):
        """accept_disclaimer returns False when the user does not exist."""
        from unittest.mock import AsyncMock, patch
        from uuid import uuid4

        from softarr.auth.service import AuthService

        uid = uuid4()
        db = AsyncMock()
        svc = AuthService(db)

        with patch.object(svc, "get_user_by_id", new=AsyncMock(return_value=None)):
            result = await svc.accept_disclaimer(uid)

        assert result is False
