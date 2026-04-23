"""Unit tests for Phase 0 roadmap features.

Covers:
  - Pagination schema (PaginatedReleaseResponse)
  - Release service: get_filtered_releases, bulk_approve, bulk_reject, bulk_delete,
    delete_old_discovered, get_by_version
  - Action service: send_nzb_to_sabnzbd
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from softarr.models.release import FlagStatus, WorkflowState
from softarr.schemas.release import PaginatedReleaseResponse
from softarr.services.release_service import ReleaseService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service():
    db = AsyncMock()
    ini = MagicMock()
    ini.get = MagicMock(return_value="false")
    ini.get_enabled_indexer_configs = MagicMock(return_value=[])
    return ReleaseService(db, ini)


def _fake_release(workflow_state=WorkflowState.DISCOVERED, version="1.0.0"):
    r = MagicMock()
    r.id = uuid4()
    r.version = version
    r.workflow_state = workflow_state
    r.flag_status = FlagStatus.NONE
    r.created_at = datetime.now(timezone.utc)
    r.software = None
    return r


# ---------------------------------------------------------------------------
# PaginatedReleaseResponse
# ---------------------------------------------------------------------------


class TestPaginatedReleaseResponse:
    def test_build_calculates_total_pages(self):
        items = []
        result = PaginatedReleaseResponse.build(items, total=95, page=1, page_size=50)
        assert result.total_pages == 2
        assert result.total == 95
        assert result.page == 1
        assert result.page_size == 50

    def test_build_single_page(self):
        result = PaginatedReleaseResponse.build([], total=10, page=1, page_size=50)
        assert result.total_pages == 1

    def test_build_zero_total(self):
        result = PaginatedReleaseResponse.build([], total=0, page=1, page_size=50)
        assert result.total_pages == 0

    def test_build_exact_page_boundary(self):
        result = PaginatedReleaseResponse.build([], total=100, page=2, page_size=50)
        assert result.total_pages == 2


# ---------------------------------------------------------------------------
# get_filtered_releases
# ---------------------------------------------------------------------------


class TestGetFilteredReleases:
    @pytest.mark.asyncio
    async def test_returns_tuple_with_count(self):
        service = _make_service()
        fake_release = _fake_release()
        call_count = 0

        async def fake_execute(_):
            nonlocal call_count
            result = AsyncMock()
            if call_count == 0:
                # Count query
                result.scalar_one = MagicMock(return_value=1)
            else:
                # Rows query
                scalars_result = MagicMock()
                scalars_result.all = MagicMock(return_value=[fake_release])
                result.scalars = MagicMock(return_value=scalars_result)
            call_count += 1
            return result

        service.db.execute = fake_execute
        releases, total = await service.get_filtered_releases(page=1, page_size=50)
        assert total == 1
        assert len(releases) == 1

    @pytest.mark.asyncio
    async def test_empty_result(self):
        service = _make_service()
        call_count = 0

        async def fake_execute(_):
            nonlocal call_count
            result = AsyncMock()
            if call_count == 0:
                result.scalar_one = MagicMock(return_value=0)
            else:
                scalars_result = MagicMock()
                scalars_result.all = MagicMock(return_value=[])
                result.scalars = MagicMock(return_value=scalars_result)
            call_count += 1
            return result

        service.db.execute = fake_execute
        releases, total = await service.get_filtered_releases()
        assert total == 0
        assert releases == []

    @pytest.mark.asyncio
    async def test_invalid_enum_filters_ignored(self):
        """Unknown enum values should not raise -- they are silently skipped."""
        service = _make_service()
        call_count = 0

        async def fake_execute(_):
            nonlocal call_count
            result = AsyncMock()
            if call_count == 0:
                result.scalar_one = MagicMock(return_value=0)
            else:
                scalars_result = MagicMock()
                scalars_result.all = MagicMock(return_value=[])
                result.scalars = MagicMock(return_value=scalars_result)
            call_count += 1
            return result

        service.db.execute = fake_execute
        # Should not raise
        releases, total = await service.get_filtered_releases(
            trust_status="nonexistent_value",
            flag_status="also_invalid",
            workflow_state="not_real",
        )
        assert total == 0


# ---------------------------------------------------------------------------
# bulk_approve / bulk_reject / bulk_delete
# ---------------------------------------------------------------------------


class TestBulkApprove:
    @pytest.mark.asyncio
    async def test_returns_succeeded_and_failed(self):
        service = _make_service()
        id1 = uuid4()
        id2 = uuid4()

        fake_release = _fake_release(workflow_state=WorkflowState.UNDER_REVIEW)
        fake_release.id = id1

        approve_calls = []

        async def fake_approve(release_id, approved_by="admin"):
            approve_calls.append(release_id)
            return fake_release

        service.approve_release = fake_approve

        result = await service.bulk_approve([id1, id2], user="admin")
        assert isinstance(result, dict)
        assert "succeeded" in result
        assert "failed" in result

    @pytest.mark.asyncio
    async def test_failed_items_captured(self):
        service = _make_service()
        bad_id = uuid4()

        async def fake_approve(release_id, approved_by="admin"):
            raise ValueError("Release not found")

        service.approve_release = fake_approve

        result = await service.bulk_approve([bad_id])
        assert str(bad_id) in [f["id"] for f in result["failed"]]
        assert len(result["succeeded"]) == 0


class TestBulkReject:
    @pytest.mark.asyncio
    async def test_rejects_valid_releases(self):
        service = _make_service()
        id1 = uuid4()

        fake_release = _fake_release(workflow_state=WorkflowState.DISCOVERED)
        fake_release.id = id1
        fake_release.workflow_state = WorkflowState.DISCOVERED

        transition_calls = []

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=fake_release)
            scalars_result = MagicMock()
            scalars_result.all = MagicMock(return_value=[fake_release])
            result.scalars = MagicMock(return_value=scalars_result)
            return result

        service.db.execute = fake_execute
        service.db.commit = AsyncMock()
        service.db.refresh = AsyncMock()

        result = await service.bulk_reject([id1], user="admin")
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_cannot_reject_downloaded(self):
        service = _make_service()
        bad_id = uuid4()

        fake_release = _fake_release(workflow_state=WorkflowState.DOWNLOADED)
        fake_release.id = bad_id

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=fake_release)
            return result

        service.db.execute = fake_execute

        result = await service.bulk_reject([bad_id])
        assert len(result["failed"]) == 1
        assert "Cannot reject" in result["failed"][0]["error"]


class TestBulkDelete:
    @pytest.mark.asyncio
    async def test_deletes_existing(self):
        service = _make_service()
        id1 = uuid4()

        async def fake_delete(release_id):
            return True

        service.delete_release = fake_delete

        result = await service.bulk_delete([id1])
        assert str(id1) in result["succeeded"]
        assert len(result["failed"]) == 0

    @pytest.mark.asyncio
    async def test_failed_when_not_found(self):
        service = _make_service()
        id1 = uuid4()

        async def fake_delete(release_id):
            return False

        service.delete_release = fake_delete

        result = await service.bulk_delete([id1])
        assert str(id1) in [f["id"] for f in result["failed"]]


# ---------------------------------------------------------------------------
# delete_old_discovered
# ---------------------------------------------------------------------------


class TestDeleteOldDiscovered:
    @pytest.mark.asyncio
    async def test_deletes_and_returns_count(self):
        service = _make_service()

        r1 = _fake_release(workflow_state=WorkflowState.DISCOVERED)
        r2 = _fake_release(workflow_state=WorkflowState.DISCOVERED)

        async def fake_execute(_):
            result = AsyncMock()
            scalars_result = MagicMock()
            scalars_result.all = MagicMock(return_value=[r1, r2])
            result.scalars = MagicMock(return_value=scalars_result)
            return result

        service.db.execute = fake_execute
        service.db.delete = AsyncMock()
        service.db.commit = AsyncMock()

        count = await service.delete_old_discovered(older_than_days=30)
        assert count == 2
        assert service.db.delete.call_count == 2
        service.db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_zero_when_nothing_to_delete(self):
        service = _make_service()

        async def fake_execute(_):
            result = AsyncMock()
            scalars_result = MagicMock()
            scalars_result.all = MagicMock(return_value=[])
            result.scalars = MagicMock(return_value=scalars_result)
            return result

        service.db.execute = fake_execute
        service.db.delete = AsyncMock()
        service.db.commit = AsyncMock()

        count = await service.delete_old_discovered(older_than_days=30)
        assert count == 0
        service.db.delete.assert_not_called()


# ---------------------------------------------------------------------------
# get_by_version
# ---------------------------------------------------------------------------


class TestGetByVersion:
    @pytest.mark.asyncio
    async def test_returns_release_when_found(self):
        service = _make_service()
        fake_release = _fake_release(version="2.1.0")

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=fake_release)
            return result

        service.db.execute = fake_execute
        release = await service.get_by_version(uuid4(), "2.1.0")
        assert release is fake_release

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        service = _make_service()

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=None)
            return result

        service.db.execute = fake_execute
        release = await service.get_by_version(uuid4(), "99.0.0")
        assert release is None


# ---------------------------------------------------------------------------
# send_nzb_to_sabnzbd in ActionService
# ---------------------------------------------------------------------------


class TestSendNzbToSabnzbd:
    @pytest.mark.asyncio
    async def test_successful_nzb_upload(self):
        from softarr.services.action_service import ActionService

        db = AsyncMock()
        db.add = (
            MagicMock()
        )  # Session.add() is synchronous; AsyncMock is incorrect here
        ini = MagicMock()
        ini.get = MagicMock(
            side_effect=lambda k: {
                "sabnzbd_url": "http://sabnzbd.local",
                "sabnzbd_api_key": "testkey",
                "sabnzbd_category": "software",
                "sabnzbd_ssl_verify": "true",
                "sabnzbd_timeout": "30",
            }.get(k, "")
        )

        service = ActionService(db, ini)
        release_id = uuid4()

        fake_release = MagicMock()
        fake_release.id = release_id
        fake_release.workflow_state = WorkflowState.APPROVED
        fake_release.name = "TestApp"
        fake_release.version = "1.0.0"

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=fake_release)
            return result

        db.execute = fake_execute
        db.commit = AsyncMock()
        db.refresh = AsyncMock(return_value=fake_release)

        with patch("softarr.services.action_service.SABnzbdClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.send_nzb_content = AsyncMock(
                return_value={"status": True, "nzo_ids": ["abc123"]}
            )
            MockClient.return_value = mock_client_instance

            result = await service.send_nzb_to_sabnzbd(
                release_id=release_id,
                nzb_bytes=b"<nzb>test</nzb>",
                filename="test.nzb",
                user="admin",
            )

        assert result["status"] == "queued"
        assert result["release_id"] == str(release_id)
        mock_client_instance.send_nzb_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_when_not_approved(self):
        from softarr.services.action_service import ActionError, ActionService

        db = AsyncMock()
        ini = MagicMock()
        ini.get = MagicMock(
            side_effect=lambda k: {
                "sabnzbd_url": "http://sabnzbd.local",
                "sabnzbd_api_key": "testkey",
                "sabnzbd_category": "software",
                "sabnzbd_ssl_verify": "true",
                "sabnzbd_timeout": "30",
            }.get(k, "")
        )

        service = ActionService(db, ini)
        release_id = uuid4()

        fake_release = MagicMock()
        fake_release.id = release_id
        fake_release.workflow_state = WorkflowState.DISCOVERED

        async def fake_execute(_):
            result = AsyncMock()
            result.scalar_one_or_none = MagicMock(return_value=fake_release)
            return result

        db.execute = fake_execute

        with pytest.raises(ActionError, match="APPROVED"):
            await service.send_nzb_to_sabnzbd(
                release_id=release_id,
                nzb_bytes=b"<nzb/>",
                filename="test.nzb",
                user="admin",
            )
