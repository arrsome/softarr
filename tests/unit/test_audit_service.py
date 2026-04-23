"""Tests for AuditService pruning and counting."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_prune_removes_old_logs():
    """prune_old_logs should delete logs older than retention_days."""
    from softarr.services.audit_service import AuditService

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=3))

    svc = AuditService(db)
    count = await svc.prune_old_logs(retention_days=30)

    assert db.execute.called
    # commit should have been called
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_prune_keeps_recent_logs():
    """prune_old_logs with a large retention_days should delete nothing."""
    from softarr.services.audit_service import AuditService

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=0))

    svc = AuditService(db)
    count = await svc.prune_old_logs(retention_days=36500)  # 100 years

    assert db.execute.called
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_count_logs_returns_integer():
    """count_logs should return an integer from the scalar result."""
    from softarr.services.audit_service import AuditService

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one.return_value = 42
    db.execute = AsyncMock(return_value=result_mock)

    svc = AuditService(db)
    count = await svc.count_logs()

    assert count == 42


@pytest.mark.asyncio
async def test_count_logs_returns_zero_when_none():
    """count_logs should return 0 when scalar is None."""
    from softarr.services.audit_service import AuditService

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one.return_value = None
    db.execute = AsyncMock(return_value=result_mock)

    svc = AuditService(db)
    count = await svc.count_logs()

    assert count == 0
