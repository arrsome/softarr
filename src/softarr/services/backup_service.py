"""Scheduled backup service.

Copies ``softarr.ini`` and the SQLite database to a configurable backup
directory on a schedule. SQLite is the only supported backend.

Backup filenames use an ISO timestamp suffix:
  softarr_20260407_120000.ini
  softarr_20260407_120000.db

Old backups are pruned to ``backup_keep_count`` (default 7).
"""

import asyncio
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from softarr.core.config import settings
from softarr.core.ini_settings import IniSettingsManager

logger = logging.getLogger("softarr.backup")


class BackupService:
    def __init__(self, ini: IniSettingsManager) -> None:
        self.ini = ini

    async def run_backup(self) -> Dict[str, Any]:
        """Copy softarr.ini and SQLite DB to the configured backup directory.

        Returns a dict with:
          status: "ok" | "skipped" | "error"
          files: list of created file paths
          pruned: number of old backups removed
          error: error message (only when status="error")
        """
        backup_dir = self.ini.get("backup_dir") or ""
        if not backup_dir:
            return {
                "status": "skipped",
                "reason": "backup_dir is not configured",
                "files": [],
                "pruned": 0,
            }

        # Sanity check: only SQLite is supported.
        db_url = settings.DATABASE_URL
        if "sqlite" not in db_url:
            return {
                "status": "skipped",
                "reason": "Backup only supports SQLite databases",
                "files": [],
                "pruned": 0,
            }

        try:
            backup_path = Path(backup_dir)
            backup_path.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            created_files: List[str] = []

            # -- Copy softarr.ini --
            ini_src = Path(self.ini._path)
            if ini_src.exists():
                ini_dst = backup_path / f"softarr_{timestamp}.ini"
                await asyncio.get_event_loop().run_in_executor(
                    None, shutil.copy2, str(ini_src), str(ini_dst)
                )
                # Restrictive permissions on the backup (contains secrets)
                try:
                    os.chmod(ini_dst, 0o600)
                except OSError:
                    pass
                created_files.append(str(ini_dst))

            # -- Copy SQLite DB --
            # Extract path from DATABASE_URL: sqlite+aiosqlite:///./softarr.db
            db_path_str = db_url.replace("sqlite+aiosqlite:///", "").replace(
                "sqlite:///", ""
            )
            db_src = Path(db_path_str)
            if db_src.exists():
                db_dst = backup_path / f"softarr_{timestamp}.db"
                await asyncio.get_event_loop().run_in_executor(
                    None, shutil.copy2, str(db_src), str(db_dst)
                )
                created_files.append(str(db_dst))

            # -- Prune old backups --
            keep_count = int(self.ini.get("backup_keep_count") or "7")
            pruned = await asyncio.get_event_loop().run_in_executor(
                None, self._prune_old_backups, backup_path, keep_count
            )

            return {"status": "ok", "files": created_files, "pruned": pruned}

        except Exception as exc:
            logger.error("Backup failed: %s", exc)
            return {"status": "error", "error": str(exc), "files": [], "pruned": 0}

    @staticmethod
    def _prune_old_backups(backup_path: Path, keep_count: int) -> int:
        """Remove oldest backups so that at most keep_count sets remain.

        Returns the number of files deleted.
        """
        if keep_count <= 0:
            return 0

        # Group files by timestamp prefix (softarr_YYYYMMDD_HHMMSS.*)
        import re

        pattern = re.compile(r"^softarr_(\d{8}_\d{6})\.(ini|db)$")
        timestamps: set[str] = set()
        for f in backup_path.iterdir():
            m = pattern.match(f.name)
            if m:
                timestamps.add(m.group(1))

        sorted_timestamps = sorted(timestamps)
        to_remove = sorted_timestamps[: max(0, len(sorted_timestamps) - keep_count)]

        deleted = 0
        for ts in to_remove:
            for ext in ("ini", "db"):
                f = backup_path / f"softarr_{ts}.{ext}"
                if f.exists():
                    try:
                        f.unlink()
                        deleted += 1
                    except OSError:
                        pass
        return deleted
