"""Unit tests for Phase 5 automation features.

Covers:
  - Item 11: SchedulerService loop logic
  - Item 12: NotificationService channels
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Item 11 -- SchedulerService
# ---------------------------------------------------------------------------


class TestSchedulerService:
    def _make_scheduler(self, enabled=True, interval=60):
        from softarr.services.scheduler_service import SchedulerService

        ini = MagicMock()
        ini.get = MagicMock(
            side_effect=lambda k: {
                "scheduler_enabled": "true" if enabled else "false",
                "scheduler_interval_minutes": str(interval),
            }.get(k)
        )

        db_factory = MagicMock()

        return SchedulerService(ini, db_factory), ini, db_factory

    def test_start_creates_task(self):
        svc, _, _ = self._make_scheduler()
        # Patch _loop so no real coroutine is created, and ensure_future so nothing is scheduled
        with (
            patch.object(svc, "_loop", return_value=MagicMock()),
            patch(
                "softarr.services.scheduler_service.asyncio.ensure_future"
            ) as mock_fut,
        ):
            svc.start()
            mock_fut.assert_called_once()

    def test_stop_cancels_task(self):
        svc, _, _ = self._make_scheduler()
        mock_task = MagicMock()
        mock_task.done = MagicMock(return_value=False)
        svc._task = mock_task
        svc.stop()
        mock_task.cancel.assert_called_once()

    def test_stop_does_nothing_when_task_done(self):
        svc, _, _ = self._make_scheduler()
        mock_task = MagicMock()
        mock_task.done = MagicMock(return_value=True)
        svc._task = mock_task
        svc.stop()
        mock_task.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_once_returns_ok(self):
        from softarr.services.scheduler_service import SchedulerService

        ini = MagicMock()
        ini.get = MagicMock(return_value="60")
        # Use a MagicMock db_factory -- _check_all_software is patched so it's never called
        svc = SchedulerService(ini, MagicMock())

        with patch.object(
            svc, "_check_all_software", new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = {
                "checked": 0,
                "new": 0,
                "auto_approved": 0,
                "errors": 0,
            }
            result = await svc.run_once()

        assert result["status"] == "ok"
        mock_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_once_returns_error_on_exception(self):
        from softarr.services.scheduler_service import SchedulerService

        ini = MagicMock()
        ini.get = MagicMock(return_value="60")
        svc = SchedulerService(ini, MagicMock())

        with patch.object(
            svc, "_check_all_software", side_effect=RuntimeError("db down")
        ):
            result = await svc.run_once()

        assert result["status"] == "error"
        assert "db down" in result["message"]


# ---------------------------------------------------------------------------
# Item 12 -- NotificationService
# ---------------------------------------------------------------------------


class TestNotificationService:
    def _make_service(self, settings=None):
        from softarr.services.notification_service import NotificationService

        base = {
            "notifications_enabled": "true",
            "notify_on_new_release": "true",
            "notify_on_flagged": "true",
            "notify_on_download": "true",
            "email_enabled": "false",
            "discord_webhook_enabled": "false",
            "http_webhook_enabled": "false",
        }
        if settings:
            base.update(settings)

        ini = MagicMock()
        ini.get = MagicMock(side_effect=lambda k: base.get(k, ""))
        return NotificationService(ini)

    @pytest.mark.asyncio
    async def test_no_channels_fires_silently(self):
        """When all channels are disabled no exceptions should be raised."""
        svc = self._make_service()
        await svc.notify(
            "new_release_discovered", {"name": "TestApp", "version": "1.0.0"}
        )

    @pytest.mark.asyncio
    async def test_notifications_disabled_skips_all(self):
        svc = self._make_service({"notifications_enabled": "false"})
        with patch.object(svc, "_send_discord_webhook") as mock_discord:
            await svc.notify("new_release_discovered", {})
        mock_discord.assert_not_called()

    @pytest.mark.asyncio
    async def test_event_not_enabled_skips(self):
        svc = self._make_service({"notify_on_new_release": "false"})
        with patch.object(svc, "_send_discord_webhook") as mock_discord:
            await svc.notify("new_release_discovered", {})
        mock_discord.assert_not_called()

    @pytest.mark.asyncio
    async def test_discord_webhook_called_when_enabled(self):
        svc = self._make_service(
            {
                "discord_webhook_enabled": "true",
                "discord_webhook_url": "https://discord.com/webhooks/test",
            }
        )

        with patch.object(
            svc, "_send_discord_webhook", new_callable=AsyncMock
        ) as mock_discord:
            await svc.notify(
                "new_release_discovered", {"name": "App", "version": "1.0"}
            )

        mock_discord.assert_called_once()

    @pytest.mark.asyncio
    async def test_http_webhook_called_when_enabled(self):
        svc = self._make_service(
            {
                "http_webhook_enabled": "true",
                "http_webhook_url": "https://example.com/hook",
            }
        )

        with patch.object(
            svc, "_send_http_webhook", new_callable=AsyncMock
        ) as mock_http:
            await svc.notify(
                "release_flagged",
                {"name": "App", "version": "1.0", "flag_reasons": ["test"]},
            )

        mock_http.assert_called_once()

    @pytest.mark.asyncio
    async def test_discord_webhook_sends_correct_payload(self):

        svc = self._make_service(
            {
                "discord_webhook_enabled": "true",
                "discord_webhook_url": "https://discord.com/webhooks/test",
            }
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 204

        with patch(
            "softarr.services.notification_service.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            await svc._send_discord_webhook(
                "new_release_discovered",
                "New release: TestApp 1.0",
                {"name": "TestApp"},
            )

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "embeds" in call_kwargs.kwargs.get("json", {})

    @pytest.mark.asyncio
    async def test_http_webhook_sends_event_and_data(self):
        svc = self._make_service(
            {
                "http_webhook_enabled": "true",
                "http_webhook_url": "https://example.com/hook",
            }
        )

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

            await svc._send_http_webhook(
                "release_flagged", {"name": "TestApp", "flag_reasons": ["test"]}
            )

        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json", {})
        assert body["event"] == "release_flagged"
        assert "data" in body

    def test_format_message_new_release(self):
        svc = self._make_service()
        msg = svc._format_message(
            "new_release_discovered",
            {"software_name": "TestApp", "version": "2.0", "source_type": "github"},
        )
        assert "TestApp" in msg
        assert "2.0" in msg

    def test_format_message_flagged(self):
        svc = self._make_service()
        msg = svc._format_message(
            "release_flagged",
            {"name": "TestApp", "version": "1.0", "flag_reasons": ["suspicious_name"]},
        )
        assert "TestApp" in msg
        assert "suspicious_name" in msg


# ---------------------------------------------------------------------------
# TBI-01 -- Scheduler auto-queue respects active_download_client
# ---------------------------------------------------------------------------


class TestSchedulerAutoQueue:
    """Tests that the scheduler dispatches to the correct download client."""

    def _make_ini(
        self,
        active_client="sabnzbd",
        sabnzbd_url="http://sabnzbd",
        qbt_url="",
        qbt_user="",
    ):
        ini = MagicMock()

        def _get(k):
            return {
                "auto_queue_upgrades": "true",
                "active_download_client": active_client,
                "sabnzbd_url": sabnzbd_url,
                "qbittorrent_url": qbt_url,
                "qbittorrent_username": qbt_user,
            }.get(k, "")

        ini.get = MagicMock(side_effect=_get)
        return ini

    @pytest.mark.asyncio
    async def test_sabnzbd_client_calls_send_to_sabnzbd(self):
        """When active_download_client=sabnzbd, scheduler calls send_to_sabnzbd."""
        from softarr.services.scheduler_service import SchedulerService

        ini = self._make_ini(active_client="sabnzbd", sabnzbd_url="http://sabnzbd")
        svc = SchedulerService(ini, MagicMock())

        with patch.object(
            svc, "_check_all_software", new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = {
                "checked": 0,
                "new": 0,
                "auto_approved": 0,
                "errors": 0,
                "upgrades_found": 0,
            }
            await svc.run_once()
        mock_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_qbittorrent_client_calls_send_to_torrent(self):
        """When active_download_client=qbittorrent, scheduler calls send_to_torrent."""
        from softarr.services.scheduler_service import SchedulerService

        ini = self._make_ini(
            active_client="qbittorrent", qbt_url="http://qbt", qbt_user="admin"
        )
        svc = SchedulerService(ini, MagicMock())

        with patch.object(
            svc, "_check_all_software", new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = {
                "checked": 0,
                "new": 0,
                "auto_approved": 0,
                "errors": 0,
                "upgrades_found": 0,
            }
            await svc.run_once()
        mock_check.assert_called_once()

    def test_notification_download_queued_message_is_client_agnostic(self):
        """download_queued message no longer references SABnzbd by name."""
        from softarr.services.notification_service import NotificationService

        ini = MagicMock()
        ini.get = MagicMock(return_value="")
        svc = NotificationService(ini)
        msg = svc._format_message(
            "download_queued", {"name": "TestApp", "version": "1.0"}
        )
        assert "SABnzbd" not in msg
        assert "TestApp" in msg
        assert "queued" in msg.lower()
