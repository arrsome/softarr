import asyncio
import logging
from typing import Dict

from sqlalchemy.ext.asyncio import AsyncSession

from softarr.analysis.engine import AnalysisEngine
from softarr.models.analysis import ReleaseAnalysis
from softarr.models.release import FlagStatus, Release

logger = logging.getLogger("softarr.analysis")


class AnalysisService:
    def __init__(self, db: AsyncSession, ini=None):
        self.db = db
        self.ini = ini  # Optional -- passed when hash source lookups are needed

    async def analyze_release(self, release: Release, raw_data: Dict = None) -> Dict:
        """Run the full analysis pipeline on a release.

        If an IniSettingsManager is available, optional external hash lookups
        (VirusTotal, NSRL) are performed when a sha256 hash is present in raw_data.
        """
        raw_data = raw_data or {}
        release_data = {
            "publisher": release.publisher,
            "expected_publisher": None,
            "version": release.version,
            "source_type": release.source_type,
            "source_origin": release.source_origin,
            "raw_data": raw_data,
        }

        # Load software definition for expected_publisher
        if release.software:
            release_data["expected_publisher"] = release.software.expected_publisher

        # Load heuristic sensitivity and anti-piracy flag from INI
        sensitivity = "medium"
        antipiracy_enabled = False
        if self.ini:
            sensitivity = (self.ini.get("heuristic_sensitivity") or "medium").lower()
            antipiracy_enabled = (
                self.ini.get("antipiracy_enabled") or "false"
            ).lower() == "true"

        results = AnalysisEngine.analyze(
            release_data,
            sensitivity=sensitivity,
            antipiracy_enabled=antipiracy_enabled,
        )

        # Optional external hash lookups
        sha256 = raw_data.get("sha256") or raw_data.get("sha256_hash")
        if not sha256 and self.ini and release.source_origin:
            # Try to fetch checksums from vendor page before falling back to lookups
            sha256 = await self._fetch_vendor_checksum(release.source_origin, raw_data)
        if sha256 and self.ini:
            results = await self._run_hash_lookups(sha256, results, release=release)
            # Persist hash intelligence records asynchronously
            asyncio.create_task(self._store_hash_intelligence(release.id, sha256))

        analysis = ReleaseAnalysis(
            release_id=release.id,
            signature_status=results["signature_status"],
            hash_status=results["hash_status"],
            unusual_file_detection=results["unusual_file_detection"],
            suspicious_naming=results["suspicious_naming"],
            source_trust_score=results["source_trust_score"],
            match_quality_score=results["match_quality_score"],
        )
        self.db.add(analysis)

        # Update release with analysis results
        release.confidence_score = results["confidence_score"]
        release.flag_status = results["flag_status"]
        release.flag_reasons = results["flag_reasons"]
        release.unusual_files = results["unusual_file_detection"]
        release.suspicious_patterns = results["suspicious_naming"]

        await self.db.commit()
        await self.db.refresh(analysis)
        await self.db.refresh(release)

        # Notify if release was flagged (fire-and-forget)
        from softarr.models.release import FlagStatus as FS

        if self.ini and results.get("flag_status") not in (FS.NONE, None):
            asyncio.create_task(self._notify_flagged(release, results))

        return results

    async def _notify_flagged(self, release, results: Dict) -> None:
        """Send release-flagged notification (fire-and-forget)."""
        try:
            from softarr.services.notification_service import NotificationService

            notif = NotificationService(self.ini)
            sw_name = (
                release.software.canonical_name if release.software else release.name
            )
            await notif.notify(
                "release_flagged",
                {
                    "software_name": sw_name,
                    "name": release.name,
                    "version": release.version,
                    "flag_status": results.get("flag_status", "").value
                    if hasattr(results.get("flag_status", ""), "value")
                    else str(results.get("flag_status", "")),
                    "flag_reasons": results.get("flag_reasons", []),
                    "release_id": str(release.id),
                },
            )
        except Exception as exc:
            logger.warning("Flagged notification failed: %s", exc)

    async def _store_hash_intelligence(self, release_id, sha256: str) -> None:
        """Persist hash intelligence records for a release (fire-and-forget)."""
        if not self.ini:
            return
        try:
            from softarr.core.database import AsyncSessionLocal
            from softarr.services.hash_intelligence_service import (
                HashIntelligenceService,
            )

            async with AsyncSessionLocal() as db:
                svc = HashIntelligenceService(db, self.ini)
                await svc.check_all_sources(release_id, sha256=sha256)
        except Exception as exc:
            logger.warning("Hash intelligence store failed: %s", exc)

    async def _fetch_vendor_checksum(self, source_origin: str, raw_data: Dict) -> str:
        """Attempt to fetch a SHA-256 checksum from the vendor page co-located with the release URL.

        Returns the hash string on success, or empty string if not found.
        """
        try:
            from softarr.analysis.hash_sources.vendor_checksums import (
                fetch_vendor_checksums,
            )

            filename = source_origin.rstrip("/").rsplit("/", 1)[-1] or ""
            checksums = await fetch_vendor_checksums(source_origin, filename)
            if checksums:
                sha256 = checksums.get("sha256") or ""
                if sha256:
                    raw_data["sha256"] = sha256
                    logger.info("Fetched vendor checksum sha256=%s...", sha256[:16])
                    return sha256
        except Exception as exc:
            logger.debug("Vendor checksum fetch skipped: %s", exc)
        return ""

    async def _run_hash_lookups(
        self, sha256: str, results: Dict, release: Release = None
    ) -> Dict:
        """Run enabled external hash verification services and update results."""
        flags = list(results.get("flag_reasons", []))
        match_quality = results.get("match_quality_score", 0.5)

        # VirusTotal
        vt_enabled = (self.ini.get("virustotal_enabled") or "false").lower() == "true"
        if vt_enabled:
            vt_api_key = self.ini.get("virustotal_api_key") or ""
            if vt_api_key:
                from softarr.analysis.hash_sources.virustotal import lookup as vt_lookup

                vt_result = await vt_lookup(sha256, vt_api_key)
                if (
                    vt_result
                    and not vt_result.get("found")
                    and release
                    and release.source_origin
                ):
                    # Hash unknown to VT -- submit the URL for future analysis
                    from softarr.analysis.hash_sources.virustotal import (
                        submit_url_for_analysis,
                    )

                    asyncio.create_task(
                        submit_url_for_analysis(release.source_origin, vt_api_key)
                    )
                    logger.info(
                        "Queued VirusTotal URL submission for unknown hash %s...",
                        sha256[:16],
                    )
                elif (
                    vt_result
                    and vt_result.get("found")
                    and vt_result.get("malicious_count", 0) > 0
                ):
                    n = vt_result["malicious_count"]
                    total = vt_result.get("total_engines", 0)
                    flags.append(f"VirusTotal: {n}/{total} engines flagged")
                    results["hash_status"] = "mismatch"
                    results["flag_status"] = FlagStatus.BLOCKED
                    logger.warning(
                        "VirusTotal flagged hash %s: %s/%s engines",
                        sha256[:16],
                        n,
                        total,
                    )

        # NSRL
        nsrl_enabled = (self.ini.get("nsrl_enabled") or "false").lower() == "true"
        if nsrl_enabled:
            from softarr.analysis.hash_sources.nsrl import lookup as nsrl_lookup

            nsrl_result = await nsrl_lookup(sha256)
            if nsrl_result and nsrl_result.get("found"):
                # Known-good -- boost match quality
                match_quality = min(match_quality + 0.2, 1.0)
                results["hash_status"] = "match"
                product = nsrl_result.get("product_name", "")
                logger.info("NSRL matched hash %s: %s", sha256[:16], product)

        results["flag_reasons"] = flags
        results["match_quality_score"] = round(match_quality, 3)

        # Recalculate confidence with updated scores
        confidence = (
            results["source_trust_score"] + results["match_quality_score"]
        ) / 2
        results["confidence_score"] = round(confidence, 3)

        # Recalculate flag status if not already BLOCKED by VT
        if results.get("flag_status") != FlagStatus.BLOCKED:
            severity = len(flags)
            if (
                results.get("hash_status") == "mismatch"
                or results.get("signature_status") == "invalid"
            ):
                results["flag_status"] = FlagStatus.BLOCKED
            elif severity >= 2:
                results["flag_status"] = FlagStatus.RESTRICTED
            elif severity == 1:
                results["flag_status"] = FlagStatus.WARNING
            else:
                results["flag_status"] = FlagStatus.NONE

        return results
