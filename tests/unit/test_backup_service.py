"""Tests for BackupService."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def make_ini(backup_dir="", enabled="true", keep_count="3"):
    ini = MagicMock()
    cfg = {
        "backup_enabled": enabled,
        "backup_dir": backup_dir,
        "backup_interval_hours": "24",
        "backup_keep_count": keep_count,
    }
    ini.get = MagicMock(side_effect=lambda k, *_: cfg.get(k, ""))
    ini.config_path = None
    return ini


@pytest.mark.asyncio
async def test_backup_skips_when_dir_empty():
    """BackupService should skip when backup_dir is not configured."""
    from softarr.services.backup_service import BackupService

    ini = make_ini(backup_dir="")
    svc = BackupService(ini)

    with patch("softarr.services.backup_service.settings") as mock_settings:
        mock_settings.DATABASE_URL = "sqlite:///softarr.db"
        result = await svc.run_backup()

    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_backup_creates_timestamped_files():
    """BackupService should create backup files with timestamps."""
    from softarr.services.backup_service import BackupService

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create dummy source files
        db_file = os.path.join(tmpdir, "softarr.db")
        ini_file = os.path.join(tmpdir, "softarr.ini")
        backup_dir = os.path.join(tmpdir, "backups")
        os.makedirs(backup_dir)
        Path(db_file).write_text("db content")
        Path(ini_file).write_text("[softarr]\n")

        ini = make_ini(backup_dir=backup_dir)
        ini.config_path = ini_file
        svc = BackupService(ini)

        with patch("softarr.services.backup_service.settings") as mock_settings:
            mock_settings.DATABASE_URL = f"sqlite:///{db_file}"
            result = await svc.run_backup()

        assert result["status"] == "ok"
        assert len(result.get("files", [])) > 0
        # Verify timestamped file was created
        files = os.listdir(backup_dir)
        assert any("softarr_" in f for f in files)


@pytest.mark.asyncio
async def test_backup_prunes_old_files():
    """BackupService should prune old backup sets beyond keep_count."""
    from softarr.services.backup_service import BackupService

    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "softarr.db")
        ini_file = os.path.join(tmpdir, "softarr.ini")
        backup_dir = os.path.join(tmpdir, "backups")
        os.makedirs(backup_dir)
        Path(db_file).write_text("db")
        Path(ini_file).write_text("[softarr]\n")

        # Pre-create old backup files to simulate existing backups
        for ts in ["20240101_000000", "20240102_000000", "20240103_000000"]:
            Path(os.path.join(backup_dir, f"softarr_{ts}.db")).write_text("old")
            Path(os.path.join(backup_dir, f"softarr_{ts}.ini")).write_text("old")

        ini = make_ini(backup_dir=backup_dir, keep_count="1")
        ini.config_path = ini_file
        svc = BackupService(ini)

        with patch("softarr.services.backup_service.settings") as mock_settings:
            mock_settings.DATABASE_URL = f"sqlite:///{db_file}"
            result = await svc.run_backup()

        assert result["status"] == "ok"
        # Should have pruned some old backups
        assert result.get("pruned", 0) > 0
