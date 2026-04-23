"""Hash intelligence service.

Orchestrates hash lookups across all configured sources (VirusTotal, NSRL, CIRCL
hashlookup, MalwareBazaar, MISP warninglists, vendor checksums) and persists the
results as ``HashIntelligence`` records associated with each ``Release``.

Usage::

    service = HashIntelligenceService(db, ini)
    records = await service.check_all_sources(release_id, sha256=sha256)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.core.ini_settings import IniSettingsManager
from softarr.models.hash_intelligence import HashIntelligence

logger = logging.getLogger("softarr.hash_intelligence")


def _utcnow():
    return datetime.now(timezone.utc)


class HashIntelligenceService:
    def __init__(self, db: AsyncSession, ini: IniSettingsManager):
        self.db = db
        self.ini = ini

    def _get_bool(self, key: str) -> bool:
        return (self.ini.get(key) or "false").lower() == "true"

    def _recheck_after(self, hours: int) -> datetime:
        return _utcnow() + timedelta(hours=hours)

    async def check_all_sources(
        self,
        release_id: UUID,
        sha256: Optional[str] = None,
        sha1: Optional[str] = None,
        md5: Optional[str] = None,
    ) -> List[HashIntelligence]:
        """Run all enabled hash sources and persist results.

        Returns a list of newly created ``HashIntelligence`` records.
        """
        if not sha256:
            return []

        recheck_hours = int(self.ini.get("hash_recheck_interval_hours") or 24)
        records: List[HashIntelligence] = []

        # VirusTotal
        vt_enabled = self._get_bool("virustotal_enabled")
        vt_key = self.ini.get("virustotal_api_key") or ""
        if vt_enabled and vt_key:
            record = await self._check_virustotal(
                release_id, sha256, vt_key, recheck_hours
            )
            if record:
                records.append(record)

        # NSRL
        if self._get_bool("nsrl_enabled"):
            record = await self._check_nsrl(release_id, sha256, recheck_hours)
            if record:
                records.append(record)

        # CIRCL hashlookup
        if self._get_bool("circl_hashlookup_enabled"):
            record = await self._check_circl(release_id, sha256, recheck_hours)
            if record:
                records.append(record)

        # MalwareBazaar
        if self._get_bool("malwarebazaar_enabled"):
            record = await self._check_malwarebazaar(release_id, sha256, recheck_hours)
            if record:
                records.append(record)

        # MISP warninglists
        if self._get_bool("misp_warninglists_enabled"):
            record = await self._check_misp(release_id, sha256, recheck_hours)
            if record:
                records.append(record)

        # ClamAV local daemon
        if self._get_bool("clamav_enabled"):
            record = await self._check_clamav(release_id, sha256, recheck_hours)
            if record:
                records.append(record)

        for record in records:
            self.db.add(record)
        await self.db.commit()

        return records

    async def get_intelligence(self, release_id: UUID) -> List[HashIntelligence]:
        """Return all hash intelligence records for a release."""
        result = await self.db.execute(
            select(HashIntelligence)
            .where(HashIntelligence.release_id == release_id)
            .order_by(HashIntelligence.checked_at.desc())
        )
        return list(result.scalars().all())

    async def recheck_unknown(self) -> int:
        """Recheck all records with verdict "unknown" that are past their recheck_after time.

        Returns the number of records rechecked.
        """
        now = _utcnow()
        result = await self.db.execute(
            select(HashIntelligence).where(
                HashIntelligence.verdict == "unknown",
                HashIntelligence.recheck_after <= now,
                HashIntelligence.sha256.isnot(None),
            )
        )
        records = result.scalars().all()
        count = 0
        for record in records:
            new_records = await self.check_all_sources(
                record.release_id, sha256=record.sha256
            )
            if new_records:
                count += 1
        return count

    # -- Private helpers per source ----------------------------------------

    async def _check_virustotal(
        self, release_id: UUID, sha256: str, api_key: str, recheck_hours: int
    ) -> Optional[HashIntelligence]:
        from softarr.analysis.hash_sources.virustotal import lookup

        result = await lookup(sha256, api_key)
        if result is None:
            return None

        if not result.get("found"):
            verdict = "unknown"
            confidence = 0.5
        elif result.get("malicious_count", 0) > 0:
            verdict = "known_bad"
            confidence = min(
                result["malicious_count"] / max(result.get("total_engines", 1), 1), 1.0
            )
        else:
            verdict = "known_good"
            confidence = 0.9

        return HashIntelligence(
            release_id=release_id,
            sha256=sha256,
            source="virustotal",
            verdict=verdict,
            confidence=confidence,
            raw_response=result,
            recheck_after=self._recheck_after(recheck_hours)
            if verdict == "unknown"
            else None,
        )

    async def _check_nsrl(
        self, release_id: UUID, sha256: str, recheck_hours: int
    ) -> Optional[HashIntelligence]:
        from softarr.analysis.hash_sources.nsrl import lookup

        result = await lookup(sha256)
        if result is None:
            return None

        verdict = "known_good" if result.get("found") else "unknown"
        return HashIntelligence(
            release_id=release_id,
            sha256=sha256,
            source="nsrl",
            verdict=verdict,
            confidence=0.85 if verdict == "known_good" else 0.5,
            raw_response=result,
            recheck_after=self._recheck_after(recheck_hours)
            if verdict == "unknown"
            else None,
        )

    async def _check_circl(
        self, release_id: UUID, sha256: str, recheck_hours: int
    ) -> Optional[HashIntelligence]:
        from softarr.analysis.hash_sources.circl_hashlookup import lookup

        result = await lookup(sha256)
        if result is None:
            return None

        verdict = "known_good" if result.get("found") else "unknown"
        return HashIntelligence(
            release_id=release_id,
            sha256=sha256,
            source="circl",
            verdict=verdict,
            confidence=0.8 if verdict == "known_good" else 0.5,
            raw_response=result,
            recheck_after=self._recheck_after(recheck_hours)
            if verdict == "unknown"
            else None,
        )

    async def _check_malwarebazaar(
        self, release_id: UUID, sha256: str, recheck_hours: int
    ) -> Optional[HashIntelligence]:
        from softarr.analysis.hash_sources.malwarebazaar import lookup

        result = await lookup(sha256)
        if result is None:
            return None

        verdict = "known_bad" if result.get("found") else "unknown"
        return HashIntelligence(
            release_id=release_id,
            sha256=sha256,
            source="malwarebazaar",
            verdict=verdict,
            confidence=0.95 if verdict == "known_bad" else 0.5,
            raw_response=result,
            recheck_after=self._recheck_after(recheck_hours)
            if verdict == "unknown"
            else None,
        )

    async def _check_misp(
        self, release_id: UUID, sha256: str, recheck_hours: int
    ) -> Optional[HashIntelligence]:
        from softarr.analysis.hash_sources.misp_warninglists import check_hash

        result = await check_hash(sha256)
        if result is None:
            return None

        verdict = result.get("verdict", "unknown")
        return HashIntelligence(
            release_id=release_id,
            sha256=sha256,
            source="misp",
            verdict=verdict,
            confidence=0.75 if verdict == "known_good" else 0.5,
            raw_response=result,
            recheck_after=self._recheck_after(recheck_hours)
            if verdict == "unknown"
            else None,
        )

    async def _check_clamav(
        self, release_id: UUID, sha256: str, recheck_hours: int
    ) -> Optional[HashIntelligence]:
        """Query the ClamAV local daemon for the given hash.

        Uses Unix socket by default; falls back to TCP if clamav_host is set.
        """
        from softarr.analysis.hash_sources.clamav import lookup

        socket_path = self.ini.get("clamav_socket") or "/var/run/clamav/clamd.ctl"
        host = self.ini.get("clamav_host") or None
        try:
            port = int(self.ini.get("clamav_port") or 3310)
        except ValueError, TypeError:
            port = 3310

        result = await lookup(
            sha256,
            socket_path=socket_path if not host else None,
            host=host,
            port=port,
        )
        if result is None:
            # Daemon unreachable -- treat as inconclusive, schedule recheck
            return HashIntelligence(
                release_id=release_id,
                sha256=sha256,
                source="clamav",
                verdict="unknown",
                confidence=0.0,
                raw_response={"error": "daemon_unreachable"},
                recheck_after=self._recheck_after(recheck_hours),
            )

        if not result.get("found"):
            verdict = "unknown"
            confidence = 0.5
        elif result.get("infected"):
            verdict = "known_bad"
            confidence = 0.95  # High confidence: ClamAV signature match
        else:
            verdict = "known_good"
            confidence = 0.85

        return HashIntelligence(
            release_id=release_id,
            sha256=sha256,
            source="clamav",
            verdict=verdict,
            confidence=confidence,
            raw_response=result,
            recheck_after=self._recheck_after(recheck_hours)
            if verdict == "unknown"
            else None,
        )
