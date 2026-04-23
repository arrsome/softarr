"""Tests for TrustService threshold suggestions."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_suggest_threshold_needs_5_downloads():
    """suggest_threshold should return None if fewer than 5 data points."""
    from softarr.services.trust_service import TrustService

    db = AsyncMock()
    result_mock = MagicMock()
    # Only 3 clean downloads
    result_mock.all.return_value = [(0.85,), (0.90,), (0.92,)]
    db.execute = AsyncMock(return_value=result_mock)

    svc = TrustService(db)
    threshold = await svc.suggest_threshold(uuid4())
    assert threshold is None


@pytest.mark.asyncio
async def test_suggest_threshold_calculates_percentile():
    """suggest_threshold should return 10th-percentile when >= 5 downloads."""
    from softarr.services.trust_service import TrustService

    db = AsyncMock()
    result_mock = MagicMock()
    # 10 clean downloads with varying confidence
    scores = [(float(x) / 10,) for x in range(5, 15)]  # 0.5 to 1.4
    result_mock.all.return_value = scores
    db.execute = AsyncMock(return_value=result_mock)

    svc = TrustService(db)
    threshold = await svc.suggest_threshold(uuid4())

    assert threshold is not None
    assert 0.0 <= threshold <= 1.5
    # 10th percentile of 10 items is index 1
    expected = round(scores[1][0], 2)
    assert threshold == expected


@pytest.mark.asyncio
async def test_suggest_threshold_returns_none_without_db():
    """suggest_threshold should return None when db is not provided."""
    from softarr.services.trust_service import TrustService

    svc = TrustService(db=None)
    result = await svc.suggest_threshold(uuid4())
    assert result is None
