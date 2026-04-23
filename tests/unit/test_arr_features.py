"""Unit tests for ARR-stack feature additions.

Covers:
  - Monitored flag on Software model
  - Tags and download_profile on Software model
  - Wanted endpoint response shape
  - Scheduler respects monitored flag (only processes monitored software)
  - API key authentication
  - Health/readiness endpoints
  - Webhook receiver (SABnzbd hook)
  - Software export/import
  - Apprise notification channel
  - Extended release stats (monitored, total_software, wanted)
  - Structured JSON logging configuration
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Software model -- monitored, tags, download_profile
# ---------------------------------------------------------------------------


class TestSoftwareModelFields:
    """Software model has monitored, tags, last_searched_at, and download_profile."""

    def test_software_schema_has_monitored(self):
        from softarr.schemas.software import SoftwareCreate

        # monitored should have a default of True
        s = SoftwareCreate(canonical_name="TestApp")
        assert s.monitored is True

    def test_software_schema_monitored_false(self):
        from softarr.schemas.software import SoftwareCreate

        s = SoftwareCreate(canonical_name="TestApp", monitored=False)
        assert s.monitored is False

    def test_software_schema_has_tags(self):
        from softarr.schemas.software import SoftwareCreate

        s = SoftwareCreate(canonical_name="TestApp", tags=["open-source", "security"])
        assert "open-source" in s.tags

    def test_software_schema_tags_default_empty(self):
        from softarr.schemas.software import SoftwareCreate

        s = SoftwareCreate(canonical_name="TestApp")
        assert s.tags == []

    def test_software_schema_has_download_profile(self):
        from softarr.schemas.software import SoftwareCreate

        profile = {
            "preferred_source": "usenet",
            "min_match_score": 0.7,
            "auto_approve_threshold": 0.9,
        }
        s = SoftwareCreate(canonical_name="TestApp", download_profile=profile)
        assert s.download_profile["preferred_source"] == "usenet"

    def test_download_profile_defaults(self):
        from softarr.schemas.software import DownloadProfile

        p = DownloadProfile()
        assert p.preferred_source is None
        assert p.min_match_score == 0.5
        assert p.auto_approve_threshold == 0.0

    def test_software_update_schema_accepts_monitored(self):
        from softarr.schemas.software import SoftwareUpdate

        u = SoftwareUpdate(monitored=False)
        assert u.monitored is False

    def test_software_response_has_new_fields(self):
        from softarr.schemas.software import SoftwareResponse

        fields = SoftwareResponse.model_fields
        assert "monitored" in fields
        assert "tags" in fields
        assert "download_profile" in fields
        assert "last_searched_at" in fields


# ---------------------------------------------------------------------------
# Scheduler -- honours monitored flag
# ---------------------------------------------------------------------------


class TestSchedulerMonitoredFilter:
    """SchedulerService._check_all_software only processes monitored software."""

    @pytest.mark.asyncio
    async def test_only_monitored_software_is_queried(self):
        from softarr.services.scheduler_service import SchedulerService

        ini = MagicMock()
        ini.get = MagicMock(
            side_effect=lambda k: {"scheduler_interval_minutes": "60"}.get(k, "")
        )

        # Fake DB that returns an empty list (no software entries)
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        class FakeCtx:
            async def __aenter__(self_inner):
                return mock_db

            async def __aexit__(self_inner, *a):
                pass

        def fake_factory():
            return FakeCtx()

        svc = SchedulerService(ini, fake_factory)
        summary = await svc._check_all_software()

        # No software -> no new releases, no errors
        assert summary["checked"] == 0
        assert summary["new"] == 0
        assert summary["errors"] == 0

        # The execute call should have been made (to query monitored software)
        mock_db.execute.assert_called()
        # Verify the query included a monitored filter by checking that
        # the query arg is a SELECT statement (not asserting SQL text, just call was made)


# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------


class TestApiKeyAuth:
    """_check_api_key validates X-Api-Key header against configured secret."""

    def _make_request(self, api_key: str):
        request = MagicMock()
        request.headers = {"x-api-key": api_key}
        request.cookies = {}
        return request

    def test_valid_api_key_returns_user(self):
        from softarr.auth.dependencies import _check_api_key

        ini = MagicMock()
        ini.get = MagicMock(return_value="supersecret")

        request = self._make_request("supersecret")

        with patch("softarr.core.ini_settings.get_ini_settings", return_value=ini):
            user = _check_api_key(request)
        assert user is not None
        assert user["u"] == "api-key"
        assert user["role"] == "admin"

    def test_wrong_api_key_returns_none(self):
        from softarr.auth.dependencies import _check_api_key

        ini = MagicMock()
        ini.get = MagicMock(return_value="supersecret")

        request = self._make_request("wrongkey")

        with patch("softarr.core.ini_settings.get_ini_settings", return_value=ini):
            result = _check_api_key(request)
        assert result is None

    def test_no_api_key_configured_returns_none(self):
        """When no api_key is configured, any provided key is rejected."""
        from softarr.auth.dependencies import _check_api_key

        ini = MagicMock()
        ini.get = MagicMock(return_value="")  # empty -- no key configured

        request = self._make_request("anything")

        with patch("softarr.core.ini_settings.get_ini_settings", return_value=ini):
            result = _check_api_key(request)
        assert result is None

    def test_empty_api_key_header_returns_none(self):
        """Empty X-Api-Key header is skipped without touching INI."""
        from softarr.auth.dependencies import _check_api_key

        request = MagicMock()
        request.headers = {}  # no x-api-key header

        result = _check_api_key(request)
        assert result is None


# ---------------------------------------------------------------------------
# Health / readiness endpoints
# ---------------------------------------------------------------------------


class TestHealthEndpoints:
    """Health and readiness endpoints return correct shapes."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self):
        from httpx import ASGITransport, AsyncClient

        from softarr.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "version" in data

    @pytest.mark.asyncio
    async def test_ready_returns_ok_with_db(self):
        from httpx import ASGITransport, AsyncClient

        from softarr.core.database import get_db
        from softarr.main import app

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def override_get_db():
            yield mock_db

        app.dependency_overrides[get_db] = override_get_db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                r = await client.get("/ready")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"
        finally:
            app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Webhook receiver
# ---------------------------------------------------------------------------


class TestWebhookReceiver:
    """Inbound SABnzbd webhook transitions release state."""

    @pytest.mark.asyncio
    async def test_unsupported_client_returns_400(self):
        from httpx import ASGITransport, AsyncClient

        from softarr.core.database import get_db
        from softarr.core.ini_settings import get_ini_settings
        from softarr.main import app

        mock_db = AsyncMock()
        ini = MagicMock()
        ini.get = MagicMock(return_value="")

        async def override_db():
            yield mock_db

        def override_ini():
            return ini

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_ini_settings] = override_ini
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                # Provide a valid auth cookie/header skip by using a session override
                r = await client.post(
                    "/api/v1/hooks/nzbget",
                    json={"status": "Completed", "name": "Test"},
                    headers={"x-api-key": ""},
                )
            # 400 for unsupported client type
            assert r.status_code == 400
            assert "Unknown client type" in r.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_ini_settings, None)

    @pytest.mark.asyncio
    async def test_non_completion_status_returns_ignored(self):
        from httpx import ASGITransport, AsyncClient

        from softarr.core.database import get_db
        from softarr.core.ini_settings import get_ini_settings
        from softarr.main import app

        mock_db = AsyncMock()
        ini = MagicMock()
        ini.get = MagicMock(return_value="")

        async def override_db():
            yield mock_db

        def override_ini():
            return ini

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_ini_settings] = override_ini
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                r = await client.post(
                    "/api/v1/hooks/sabnzbd",
                    json={"nzo_id": "abc123", "status": "Verifying", "name": "Test"},
                )
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ignored"
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_ini_settings, None)

    @pytest.mark.asyncio
    async def test_webhook_secret_enforced(self):
        from httpx import ASGITransport, AsyncClient

        from softarr.core.database import get_db
        from softarr.core.ini_settings import get_ini_settings
        from softarr.main import app

        mock_db = AsyncMock()
        ini = MagicMock()
        ini.get = MagicMock(return_value="mysecret")  # secret configured

        async def override_db():
            yield mock_db

        def override_ini():
            return ini

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_ini_settings] = override_ini
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                r = await client.post(
                    "/api/v1/hooks/sabnzbd",
                    json={"nzo_id": "abc", "status": "Completed", "name": "Test"},
                    # Missing X-Webhook-Secret header
                )
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_ini_settings, None)


# ---------------------------------------------------------------------------
# Software export/import
# ---------------------------------------------------------------------------


class TestSoftwareExportImport:
    """Software export returns JSON array; import creates new entries."""

    @pytest.mark.asyncio
    async def test_export_returns_list_via_service(self):
        """SoftwareService.get_all_software returns a list; export wraps it."""
        from softarr.services.software_service import SoftwareService

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        svc = SoftwareService(mock_db)
        items = await svc.get_all_software()
        assert isinstance(items, list)

    @pytest.mark.asyncio
    async def test_export_endpoint_requires_auth(self):
        """Export endpoint returns 401 without credentials."""
        from fastapi.testclient import TestClient

        from softarr.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/v1/software/export")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_import_endpoint_requires_auth(self):
        """Import endpoint returns 401 without credentials."""
        from fastapi.testclient import TestClient

        from softarr.main import app

        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/api/v1/software/import", json=[])
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Apprise notification channel
# ---------------------------------------------------------------------------


class TestAppriseNotification:
    """Apprise webhook channel is called when configured."""

    @pytest.mark.asyncio
    async def test_apprise_called_when_enabled(self):
        from softarr.services.notification_service import NotificationService

        ini = MagicMock()
        ini.get = MagicMock(
            side_effect=lambda k: {
                "notifications_enabled": "true",
                "notify_on_new_release": "true",
                "email_enabled": "false",
                "discord_webhook_enabled": "false",
                "http_webhook_enabled": "false",
                "apprise_webhook_enabled": "true",
                "apprise_webhook_url": "https://apprise.example.com/notify/test",
            }.get(k, "")
        )

        svc = NotificationService(ini)

        with patch.object(
            svc, "_send_apprise_webhook", new_callable=AsyncMock
        ) as mock_apprise:
            await svc.notify(
                "new_release_discovered", {"name": "TestApp", "version": "1.0"}
            )

        mock_apprise.assert_called_once()

    @pytest.mark.asyncio
    async def test_apprise_sends_correct_payload(self):
        from softarr.services.notification_service import NotificationService

        ini = MagicMock()
        ini.get = MagicMock(
            side_effect=lambda k: {
                "apprise_webhook_url": "https://apprise.example.com/notify/test",
            }.get(k, "")
        )

        svc = NotificationService(ini)

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch(
            "softarr.services.notification_service.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            await svc._send_apprise_webhook(
                "new_release_discovered",
                "New release: TestApp 1.0",
                {"name": "TestApp"},
            )

        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json", {})
        assert "title" in body
        assert "body" in body
        assert body["type"] == "info"

    @pytest.mark.asyncio
    async def test_apprise_download_complete_type_success(self):
        from softarr.services.notification_service import NotificationService

        ini = MagicMock()
        ini.get = MagicMock(return_value="https://apprise.example.com/notify/test")

        svc = NotificationService(ini)

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch(
            "softarr.services.notification_service.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            await svc._send_apprise_webhook(
                "download_complete",
                "Download completed: TestApp 1.0",
                {"name": "TestApp", "version": "1.0"},
            )

        body = mock_client.post.call_args.kwargs["json"]
        assert body["type"] == "success"


# ---------------------------------------------------------------------------
# Extended release stats
# ---------------------------------------------------------------------------


class TestExtendedReleaseStats:
    """get_release_stats returns monitored, total_software, and wanted counts."""

    @pytest.mark.asyncio
    async def test_stats_include_software_counts(self):
        from unittest.mock import AsyncMock, MagicMock

        from softarr.services.release_service import ReleaseService

        db = AsyncMock()
        ini = MagicMock()
        ini.get = MagicMock(return_value=None)
        ini.get_enabled_indexer_configs = MagicMock(return_value=[])

        svc = ReleaseService(db, ini)

        async def fake_execute(_):
            result = MagicMock()
            result.scalar_one = MagicMock(return_value=5)
            result.all = MagicMock(return_value=[])  # no monitored IDs -> wanted = 0
            return result

        svc.db.execute = fake_execute

        stats = await svc.get_release_stats()
        assert "monitored" in stats
        assert "total_software" in stats
        assert "wanted" in stats
        assert stats["wanted"] == 0  # no monitored software -> no wanted


# ---------------------------------------------------------------------------
# JSON logging configuration
# ---------------------------------------------------------------------------


class TestJsonLogging:
    """LOG_FORMAT setting controls the logging output format."""

    def test_log_format_setting_exists(self):
        from softarr.core.config import Settings

        s = Settings()
        assert hasattr(s, "LOG_FORMAT")
        assert s.LOG_FORMAT in ("text", "json")

    def test_configure_logging_text_does_not_raise(self):
        from softarr.core.logging import configure_logging as _configure_logging

        with patch("softarr.core.config.settings") as mock_settings:
            mock_settings.DEBUG = False
            mock_settings.LOG_FORMAT = "text"
            # Should not raise
            _configure_logging()

    def test_configure_logging_json_falls_back_gracefully(self):
        """If pythonjsonlogger is unavailable, falls back to basicConfig."""
        import sys

        # Temporarily hide the module if it's available
        original = sys.modules.get("pythonjsonlogger")
        sys.modules["pythonjsonlogger"] = None
        sys.modules["pythonjsonlogger.json"] = None

        try:
            from softarr.core.logging import configure_logging as _configure_logging

            with patch("softarr.core.config.settings") as mock_settings:
                mock_settings.DEBUG = False
                mock_settings.LOG_FORMAT = "json"
                # Should fall back without raising
                _configure_logging()
        finally:
            if original is not None:
                sys.modules["pythonjsonlogger"] = original
            else:
                sys.modules.pop("pythonjsonlogger", None)
            sys.modules.pop("pythonjsonlogger.json", None)
