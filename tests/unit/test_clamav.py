"""Unit tests for ClamAV hash source."""

import pytest

from softarr.analysis.hash_sources.clamav import (
    _clamd_command,
    lookup,
    scan_file,
)


class TestClamdCommand:
    @pytest.mark.asyncio
    async def test_returns_none_on_connection_refused(self):
        # Port 1 is never open; this should return None, not raise
        result = await _clamd_command(b"zPING\0", host="127.0.0.1", port=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_bad_socket(self, tmp_path):
        # Non-existent socket path
        result = await _clamd_command(
            b"zPING\0", socket_path=str(tmp_path / "nonexistent.ctl")
        )
        assert result is None


class TestLookup:
    @pytest.mark.asyncio
    async def test_unreachable_daemon_returns_unknown(self):
        # No daemon running -- should return a structured result, not raise
        result = await lookup("a" * 64, host="127.0.0.1", port=1)
        # Returns None when the daemon is unreachable (caller handles None)
        assert result is None

    @pytest.mark.asyncio
    async def test_bad_socket_returns_none(self, tmp_path):
        result = await lookup("b" * 64, socket_path=str(tmp_path / "no.ctl"))
        assert result is None


class TestScanFile:
    @pytest.mark.asyncio
    async def test_unreachable_daemon_returns_none(self):
        result = await scan_file("/tmp/test.txt", host="127.0.0.1", port=1)
        assert result is None
