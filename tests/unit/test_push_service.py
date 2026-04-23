"""Unit tests for PushNotificationService."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from softarr.services.push_notification_service import PushNotificationService


class FakeIni:
    def __init__(self, settings: dict):
        self._settings = settings

    def get(self, key: str):
        return self._settings.get(key)


class TestPushNotificationService:
    def test_is_enabled_true(self):
        ini = FakeIni({"push_notifications_enabled": "true"})
        svc = PushNotificationService(MagicMock(), ini)
        assert svc.is_enabled() is True

    def test_is_enabled_false(self):
        ini = FakeIni({"push_notifications_enabled": "false"})
        svc = PushNotificationService(MagicMock(), ini)
        assert svc.is_enabled() is False

    @pytest.mark.asyncio
    async def test_send_to_all_skips_when_disabled(self):
        ini = FakeIni({"push_notifications_enabled": "false"})
        db = MagicMock()
        svc = PushNotificationService(db, ini)
        result = await svc.send_to_all_subscribers("Test", "Body")
        assert result["sent"] == 0
        assert "skipped" in result

    @pytest.mark.asyncio
    async def test_send_to_all_skips_when_no_vapid_keys(self):
        ini = FakeIni(
            {
                "push_notifications_enabled": "true",
                "push_vapid_public_key": "",
                "push_vapid_private_key": "",
            }
        )
        db = MagicMock()
        svc = PushNotificationService(db, ini)
        result = await svc.send_to_all_subscribers("Test", "Body")
        assert result["sent"] == 0
        assert result.get("skipped") == "vapid_keys_missing"

    @pytest.mark.asyncio
    async def test_add_subscription_creates_new(self):
        """Test that add_subscription creates a new subscription record."""

        ini = FakeIni({"push_notifications_enabled": "true"})

        # Mock DB session
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        svc = PushNotificationService(mock_db, ini)

        user_id = uuid.uuid4()
        sub = await svc.add_subscription(
            user_id=user_id,
            endpoint="https://push.example.com/sub/123",
            p256dh="abc123",
            auth="xyz789",
            user_agent="TestBrowser/1.0",
        )
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
