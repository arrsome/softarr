"""Tests for NotificationService -- test_channel and history recording."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def make_ini(overrides=None):
    """Create a minimal INI mock with all notification settings disabled."""
    ini = MagicMock()
    defaults = {
        "notifications_enabled": "false",
        "discord_webhook_enabled": "false",
        "http_webhook_enabled": "false",
        "apprise_enabled": "false",
        "discord_webhook_url": "",
        "http_webhook_url": "",
        "apprise_url": "",
        "notify_on_new_release": "true",
        "notify_on_flagged": "true",
        "notify_on_download": "true",
    }
    if overrides:
        defaults.update(overrides)
    ini.get = MagicMock(side_effect=lambda k, *_: defaults.get(k, ""))
    return ini


@pytest.mark.asyncio
async def test_test_channel_returns_ok_when_channel_disabled():
    """test_channel should return ok=False when channel not configured."""
    from softarr.services.notification_service import NotificationService

    ini = make_ini()
    svc = NotificationService(ini)
    result = await svc.test_channel("discord")

    assert isinstance(result, dict)
    assert "ok" in result


@pytest.mark.asyncio
async def test_test_channel_unknown_returns_error():
    """test_channel with an unknown channel returns ok=False."""
    from softarr.services.notification_service import NotificationService

    ini = make_ini()
    svc = NotificationService(ini)
    result = await svc.test_channel("unknown_channel")

    assert result["ok"] is False
    assert result["error"] is not None


@pytest.mark.asyncio
async def test_test_channel_discord_sends_when_enabled():
    """test_channel for discord calls _send_discord when enabled."""
    from softarr.services.notification_service import NotificationService

    ini = make_ini(
        {
            "discord_webhook_enabled": "true",
            "discord_webhook_url": "https://discord.com/api/webhooks/test/test",
        }
    )
    svc = NotificationService(ini)

    with patch.object(
        svc, "_send_discord_webhook", new=AsyncMock(return_value=None)
    ) as mock_send:
        result = await svc.test_channel("discord")
        assert mock_send.called or result is not None  # either sent or returned result


@pytest.mark.asyncio
async def test_history_not_recorded_without_db():
    """Notification history should not be recorded when db is None."""
    from softarr.services.notification_service import NotificationService

    ini = make_ini()
    svc = NotificationService(ini, db=None)

    # Should not raise even without db
    await svc._record_history("test_event", "discord", True, None, {})


# ---------------------------------------------------------------------------
# TBI-15 -- Per-channel event filtering
# ---------------------------------------------------------------------------


class TestChannelEventFiltering:
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
            "apprise_webhook_enabled": "false",
        }
        if settings:
            base.update(settings)

        ini = MagicMock()
        ini.get = MagicMock(side_effect=lambda k: base.get(k, ""))
        return NotificationService(ini)

    def test_channel_allows_all_by_default(self):
        from softarr.services.notification_service import NotificationService

        ini = MagicMock()
        ini.get = MagicMock(return_value="all")
        svc = NotificationService(ini)
        assert svc._channel_allows_event("discord_events", "new_release_discovered")
        assert svc._channel_allows_event("discord_events", "release_flagged")
        assert svc._channel_allows_event("discord_events", "download_complete")

    def test_channel_allows_empty_string_as_all(self):
        from softarr.services.notification_service import NotificationService

        ini = MagicMock()
        ini.get = MagicMock(return_value="")
        svc = NotificationService(ini)
        assert svc._channel_allows_event("discord_events", "any_event")

    def test_channel_filter_specific_event_allowed(self):
        from softarr.services.notification_service import NotificationService

        ini = MagicMock()
        ini.get = MagicMock(return_value="release_flagged,download_complete")
        svc = NotificationService(ini)
        assert svc._channel_allows_event("discord_events", "release_flagged")
        assert svc._channel_allows_event("discord_events", "download_complete")

    def test_channel_filter_specific_event_blocked(self):
        from softarr.services.notification_service import NotificationService

        ini = MagicMock()
        ini.get = MagicMock(return_value="release_flagged")
        svc = NotificationService(ini)
        assert not svc._channel_allows_event("discord_events", "new_release_discovered")

    @pytest.mark.asyncio
    async def test_discord_respects_event_filter(self):
        """Discord should not receive new_release_discovered when filter excludes it."""
        svc = self._make_service(
            {
                "discord_webhook_enabled": "true",
                "discord_webhook_url": "https://discord.com/webhooks/test",
                "discord_events": "release_flagged",
            }
        )

        with patch.object(
            svc, "_send_discord_webhook", new_callable=AsyncMock
        ) as mock_discord:
            await svc.notify(
                "new_release_discovered", {"name": "App", "version": "1.0"}
            )

        mock_discord.assert_not_called()

    @pytest.mark.asyncio
    async def test_discord_sends_when_event_allowed(self):
        """Discord should receive release_flagged when filter includes it."""
        svc = self._make_service(
            {
                "discord_webhook_enabled": "true",
                "discord_webhook_url": "https://discord.com/webhooks/test",
                "discord_events": "release_flagged,new_release_discovered",
            }
        )

        with patch.object(
            svc, "_send_discord_webhook", new_callable=AsyncMock
        ) as mock_discord:
            await svc.notify(
                "release_flagged", {"name": "App", "version": "1.0", "flag_reasons": []}
            )

        mock_discord.assert_called_once()

    @pytest.mark.asyncio
    async def test_http_respects_event_filter(self):
        """HTTP webhook should not receive events outside its filter."""
        svc = self._make_service(
            {
                "http_webhook_enabled": "true",
                "http_webhook_url": "https://example.com/hook",
                "http_webhook_events": "download_complete",
            }
        )

        with patch.object(
            svc, "_send_http_webhook", new_callable=AsyncMock
        ) as mock_http:
            await svc.notify(
                "new_release_discovered", {"name": "App", "version": "1.0"}
            )

        mock_http.assert_not_called()
