"""Unit tests for ReleaseService helper methods.

Database-dependent methods are tested with AsyncMock stubs. Pure-logic helpers
(VALID_TRANSITIONS, stats aggregation shape) are tested directly.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from softarr.models.release import WorkflowState
from softarr.services.release_service import ReleaseService


def _make_service():
    db = AsyncMock()
    ini = MagicMock()
    ini.get = MagicMock(return_value="false")
    ini.get_enabled_indexer_configs = MagicMock(return_value=[])
    return ReleaseService(db, ini)


class TestGetReleaseStats:
    """get_release_stats() returns the correct dict shape."""

    @pytest.mark.asyncio
    async def test_returns_expected_keys(self):
        service = _make_service()

        # Calls: total, safe, flagged, downloaded, monitored, total_sw,
        # then one .all() call for monitored IDs (no per-software loops needed when no IDs)
        call_count = 0

        async def fake_execute(_):
            nonlocal call_count
            result = AsyncMock()
            counts = [10, 7, 3, 5, 4, 6]
            if call_count < len(counts):
                result.scalar_one = MagicMock(return_value=counts[call_count])
            else:
                result.scalar_one = MagicMock(return_value=0)
            # For the all() call that returns monitored IDs -- return empty list
            result.all = MagicMock(return_value=[])
            call_count += 1
            return result

        service.db.execute = fake_execute

        stats = await service.get_release_stats()
        assert "total" in stats
        assert "safe" in stats
        assert "flagged" in stats
        assert "downloaded" in stats
        assert "monitored" in stats
        assert "total_software" in stats
        assert "wanted" in stats
        assert stats["total"] == 10
        assert stats["safe"] == 7
        assert stats["flagged"] == 3
        assert stats["downloaded"] == 5

    @pytest.mark.asyncio
    async def test_returns_zero_when_none(self):
        """scalar_one() returning None should be coerced to 0."""
        service = _make_service()

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one = MagicMock(return_value=None)
            result.all = MagicMock(return_value=[])
            return result

        service.db.execute = fake_execute

        stats = await service.get_release_stats()
        assert stats["total"] == 0
        assert stats["safe"] == 0
        assert stats["flagged"] == 0
        assert stats["downloaded"] == 0


class TestDeleteRelease:
    """delete_release() returns True on success, False when not found."""

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self):
        service = _make_service()

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=None)
            return result

        service.db.execute = fake_execute
        result = await service.delete_release(uuid4())
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_and_deletes(self):
        service = _make_service()
        fake_release = MagicMock()

        call_count = 0

        async def fake_execute(_):
            nonlocal call_count
            result = AsyncMock()
            if call_count == 0:
                # First execute is in get_release_by_id
                result.scalar_one_or_none = MagicMock(return_value=fake_release)
            call_count += 1
            return result

        service.db.execute = fake_execute
        service.db.delete = AsyncMock()
        service.db.commit = AsyncMock()

        result = await service.delete_release(uuid4())
        assert result is True
        service.db.delete.assert_called_once_with(fake_release)
        service.db.commit.assert_called_once()


class TestGetLatestDownloadedVersion:
    """get_latest_downloaded_version() returns version string or None."""

    @pytest.mark.asyncio
    async def test_returns_version_string(self):
        service = _make_service()

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value="26.2.1")
            return result

        service.db.execute = fake_execute
        version = await service.get_latest_downloaded_version(uuid4())
        assert version == "26.2.1"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_downloads(self):
        service = _make_service()

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=None)
            return result

        service.db.execute = fake_execute
        version = await service.get_latest_downloaded_version(uuid4())
        assert version is None


class TestTransitionStateValidation:
    """transition_state() raises ValueError for invalid transitions."""

    @pytest.mark.asyncio
    async def test_invalid_transition_raises(self):
        service = _make_service()
        fake_release = MagicMock()
        fake_release.workflow_state = WorkflowState.DOWNLOADED

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=fake_release)
            return result

        service.db.execute = fake_execute

        with pytest.raises(ValueError, match="Cannot transition"):
            await service.transition_state(uuid4(), WorkflowState.STAGED)

    @pytest.mark.asyncio
    async def test_release_not_found_raises(self):
        service = _make_service()

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=None)
            return result

        service.db.execute = fake_execute

        with pytest.raises(ValueError, match="Release not found"):
            await service.transition_state(uuid4(), WorkflowState.STAGED)
