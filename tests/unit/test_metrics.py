"""Tests for the Prometheus metrics endpoint."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_metrics_text_format():
    """GET /metrics should return plain text in Prometheus format."""
    from httpx import ASGITransport, AsyncClient

    from softarr.core.database import get_db
    from softarr.core.ini_settings import get_ini_settings
    from softarr.main import app

    mock_db = AsyncMock()
    # Mock the scalar results for counts
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_result.scalar_one_or_none.return_value = 0
    mock_db.execute = AsyncMock(return_value=mock_result)

    ini = MagicMock()
    ini.get = MagicMock(return_value="false")

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
            r = await client.get("/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        # Should contain at least one HELP line
        assert "# HELP" in r.text
        assert "# TYPE" in r.text
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_ini_settings, None)


@pytest.mark.asyncio
async def test_metrics_contains_release_counts():
    """GET /metrics response should include release count metric."""
    from httpx import ASGITransport, AsyncClient

    from softarr.core.database import get_db
    from softarr.core.ini_settings import get_ini_settings
    from softarr.main import app

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []
    mock_result.scalar_one_or_none.return_value = 5
    mock_db.execute = AsyncMock(return_value=mock_result)

    ini = MagicMock()
    ini.get = MagicMock(return_value="false")

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
            r = await client.get("/metrics")
        assert r.status_code == 200
        # The metrics output should contain softarr_releases_total
        assert "softarr_releases_total" in r.text or "softarr_software_total" in r.text
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_ini_settings, None)
