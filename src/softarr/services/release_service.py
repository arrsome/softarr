import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from softarr.adapters.base import ReleaseSearchResult
from softarr.adapters.github import GitHubAdapter
from softarr.adapters.torznab import TorznabAdapter
from softarr.adapters.usenet import UsenetAdapter
from softarr.core.ini_settings import IniSettingsManager
from softarr.models.audit import ReleaseOverride
from softarr.models.release import FlagStatus, Release, TrustStatus, WorkflowState
from softarr.models.software import Software
from softarr.schemas.release import ReleaseResponse
from softarr.services.analysis_service import AnalysisService
from softarr.services.trust_service import TrustService

logger = logging.getLogger("softarr.release_service")

# Valid workflow state transitions
VALID_TRANSITIONS = {
    WorkflowState.DISCOVERED: {WorkflowState.STAGED, WorkflowState.REJECTED},
    WorkflowState.STAGED: {
        WorkflowState.UNDER_REVIEW,
        WorkflowState.REJECTED,
        WorkflowState.DISCOVERED,
    },
    WorkflowState.UNDER_REVIEW: {
        WorkflowState.APPROVED,
        WorkflowState.REJECTED,
        WorkflowState.STAGED,
    },
    WorkflowState.APPROVED: {
        WorkflowState.QUEUED_FOR_DOWNLOAD,
        WorkflowState.REJECTED,
    },
    WorkflowState.REJECTED: {WorkflowState.STAGED},
    WorkflowState.QUEUED_FOR_DOWNLOAD: {
        WorkflowState.DOWNLOADED,
        WorkflowState.DOWNLOAD_FAILED,
        WorkflowState.APPROVED,  # allow re-queuing
    },
    WorkflowState.DOWNLOADED: set(),
    WorkflowState.DOWNLOAD_FAILED: {
        WorkflowState.APPROVED,  # allow retry
    },
}


class ReleaseService:
    def __init__(self, db: AsyncSession, ini: IniSettingsManager):
        self.db = db
        self.ini = ini
        self.adapters = {
            "github": GitHubAdapter(),
            "usenet": UsenetAdapter(),  # Indexers loaded dynamically from settings
            "torznab": TorznabAdapter(),  # Indexers loaded dynamically from settings
        }

    def _get_usenet_adapter(self) -> UsenetAdapter:
        """Load usenet adapter with indexer configs from softarr.ini."""
        enabled = (self.ini.get("usenet_adapter_enabled") or "false").lower() == "true"
        if not enabled:
            return UsenetAdapter(indexers=[], ini=self.ini)

        configs = self.ini.get_enabled_indexer_configs()
        return UsenetAdapter(indexers=configs, ini=self.ini)

    def _get_torznab_adapter(self) -> TorznabAdapter:
        """Load Torznab adapter with indexer configs from softarr.ini."""
        enabled = (self.ini.get("torznab_adapter_enabled") or "false").lower() == "true"
        if not enabled:
            return TorznabAdapter(indexers=[], ini=self.ini)

        configs = self.ini.get_enabled_torznab_configs()
        return TorznabAdapter(indexers=configs, ini=self.ini)

    # ------------------------------------------------------------------
    # Search and discovery
    # ------------------------------------------------------------------

    async def search_releases(
        self, software_id: UUID, source_type: str = "auto"
    ) -> List[ReleaseSearchResult]:
        result = await self.db.execute(
            select(Software).where(Software.id == software_id)
        )
        software = result.scalar_one_or_none()
        if not software:
            raise ValueError(f"Software not found: {software_id}")

        # Resolve which adapter to use:
        # 1. Caller-specified source_type (not "auto") takes precedence
        # 2. Per-software preferred_adapter setting
        # 3. Default to "github"
        effective_type = source_type
        if effective_type in ("auto", "") or effective_type is None:
            effective_type = software.preferred_adapter or "github"

        # Usenet and Torznab adapters need dynamic config from INI
        if effective_type == "usenet":
            adapter = self._get_usenet_adapter()
        elif effective_type == "torznab":
            adapter = self._get_torznab_adapter()
        else:
            adapter = self.adapters.get(effective_type)

        if not adapter:
            raise ValueError(f"Unknown source type: {effective_type}")

        software_dict = {
            "canonical_name": software.canonical_name,
            "aliases": software.aliases,
            "expected_publisher": software.expected_publisher,
            "supported_os": software.supported_os,
            "architecture": software.architecture,
            "source_preferences": software.source_preferences or [],
        }
        return await adapter.search_releases(software_dict)

    async def process_and_store_release(
        self, search_result: ReleaseSearchResult, software_id: UUID
    ) -> Release:
        """Full pipeline: Parse -> Analyze -> Score -> Store.

        New releases start in DISCOVERED state.
        """
        db_release = Release(
            software_id=software_id,
            name=search_result.name,
            version=search_result.version,
            supported_os=search_result.supported_os,
            architecture=search_result.architecture,
            publisher=search_result.publisher,
            source_type=search_result.source_type,
            source_origin=search_result.source_origin,
            confidence_score=0.0,
            workflow_state=WorkflowState.DISCOVERED,
            release_notes=(search_result.raw_data or {}).get("release_notes"),
        )
        self.db.add(db_release)
        await self.db.commit()
        await self.db.refresh(db_release)

        # Load software relationship for analysis
        result = await self.db.execute(
            select(Release)
            .options(selectinload(Release.software))
            .where(Release.id == db_release.id)
        )
        db_release = result.scalar_one()

        # Run analysis -- pass ini so optional hash source lookups can run
        analysis_service = AnalysisService(self.db, ini=self.ini)
        try:
            analysis_results = await analysis_service.analyze_release(
                db_release, raw_data=search_result.raw_data
            )
        except Exception as exc:
            logger.error("Analysis failed for release %s: %s", db_release.id, exc)
            analysis_results = {}
            db_release.flag_status = FlagStatus.NONE
            db_release.confidence_score = 0.0
            await self.db.commit()
            await self.db.refresh(db_release)

        # Determine trust status
        db_release.trust_status = TrustService.determine_trust_status(
            db_release, analysis_results
        )
        await self.db.commit()

        # Re-load with software relationship -- commit() expires all attributes
        # and refresh() alone does not re-eager-load relationships.
        result = await self.db.execute(
            select(Release)
            .options(selectinload(Release.software))
            .where(Release.id == db_release.id)
        )
        db_release = result.scalar_one()

        # Apply per-software rules: version pinning, auto-reject, release type filter
        software = db_release.software
        if software:
            reject_reason = await self._apply_release_rules(
                db_release, software, analysis_results
            )
            if reject_reason:
                try:
                    await self.transition_state(
                        db_release.id,
                        WorkflowState.REJECTED,
                        changed_by="rules:auto",
                    )
                    db_release.flag_reasons = (db_release.flag_reasons or []) + [
                        reject_reason
                    ]
                    await self.db.commit()
                    await self.db.refresh(db_release)
                    logger.info(
                        "Auto-rejected release %s (%s %s): %s",
                        db_release.id,
                        db_release.name,
                        db_release.version,
                        reject_reason,
                    )
                    return db_release
                except Exception as exc:
                    logger.warning(
                        "Auto-reject transition failed for %s: %s", db_release.id, exc
                    )

        # Fire notifications (fire-and-forget)
        if self.ini:
            asyncio.create_task(self._notify_new_release(db_release))

        return db_release

    async def _apply_release_rules(
        self, release: "Release", software, analysis_results: dict
    ) -> str:
        """Apply version pin, auto-reject, and type filter rules. Returns a reject reason or ''."""
        from softarr.services.release_rules_service import (
            check_auto_reject_rules,
            check_release_type_filter,
            check_version_pin,
        )

        asset_names = list(release.unusual_files or []) + list(
            release.suspicious_patterns or []
        )
        version = release.version or ""
        name = release.name or ""
        publisher = release.publisher
        expected_publisher = software.expected_publisher
        signature_status = analysis_results.get("signature_status")

        # Version pin check
        version_pin = getattr(software, "version_pin", None)
        allowed, reason = check_version_pin(version, version_pin)
        if not allowed:
            return reason

        # Auto-reject rules
        auto_reject_rules = list(getattr(software, "auto_reject_rules") or [])
        should_reject, reason = check_auto_reject_rules(
            version,
            name,
            asset_names,
            auto_reject_rules,
            publisher=publisher,
            expected_publisher=expected_publisher,
            signature_status=signature_status,
        )
        if should_reject:
            return reason

        # Release type filter
        release_type_filter = list(getattr(software, "release_type_filter") or [])
        allowed, reason = check_release_type_filter(
            version, name, asset_names, release_type_filter
        )
        if not allowed:
            return reason

        return ""

    async def _notify_new_release(self, release: Release) -> None:
        """Send new-release-discovered notification."""
        try:
            from softarr.services.notification_service import NotificationService

            notif = NotificationService(self.ini)
            sw_name = (
                release.software.canonical_name if release.software else release.name
            )
            await notif.notify(
                "new_release_discovered",
                {
                    "software_name": sw_name,
                    "name": release.name,
                    "version": release.version,
                    "source_type": release.source_type,
                    "release_id": str(release.id),
                },
            )
        except Exception as exc:
            logger.warning("Release notification failed: %s", exc)

    # ------------------------------------------------------------------
    # Workflow state management
    # ------------------------------------------------------------------

    async def transition_state(
        self,
        release_id: UUID,
        target: WorkflowState,
        changed_by: str = "system",
        reason: Optional[str] = None,
    ) -> Release:
        """Move a release to a new workflow state with validation."""
        release = await self.get_release_by_id(release_id)
        if not release:
            raise ValueError(f"Release not found: {release_id}")

        current = release.workflow_state
        allowed = VALID_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise ValueError(
                f"Cannot transition from {current.value} to {target.value}. "
                f"Allowed: {[s.value for s in allowed]}"
            )

        release.workflow_state = target
        release.workflow_changed_at = datetime.now(timezone.utc)
        release.workflow_changed_by = changed_by
        await self.db.commit()
        await self.db.refresh(release)
        return release

    async def approve_release(
        self, release_id: UUID, approved_by: str = "admin"
    ) -> Release:
        """Approve a release: transition state + apply admin trust."""
        release = await self.transition_state(
            release_id, WorkflowState.APPROVED, changed_by=approved_by
        )
        TrustService.apply_admin_verification(release)
        await self.db.commit()
        await self.db.refresh(release)
        return release

    async def override_release(
        self,
        release_id: UUID,
        overridden_by: str = "admin",
        reason: Optional[str] = None,
    ) -> Release:
        """Override a flagged release: create override record + transition."""
        release = await self.get_release_by_id(release_id)
        if not release:
            raise ValueError(f"Release not found: {release_id}")

        # Require reason for restricted/blocked releases
        if release.flag_status in (FlagStatus.RESTRICTED, FlagStatus.BLOCKED):
            if not reason:
                raise ValueError(
                    "Override reason is required for restricted or blocked releases"
                )

        # Create override record
        override = ReleaseOverride(
            release_id=release_id,
            overridden_by=overridden_by,
            override_reason=reason,
        )
        self.db.add(override)

        # Apply admin trust (per-release only, not future releases)
        TrustService.apply_admin_verification(release)

        # Move to approved
        release.workflow_state = WorkflowState.APPROVED
        release.workflow_changed_at = datetime.now(timezone.utc)
        release.workflow_changed_by = overridden_by

        await self.db.commit()
        await self.db.refresh(release)
        return release

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_releases_by_software(
        self, software_id: UUID
    ) -> List[ReleaseResponse]:
        result = await self.db.execute(
            select(Release)
            .where(Release.software_id == software_id)
            .order_by(Release.created_at.desc())
        )
        return [ReleaseResponse.model_validate(r) for r in result.scalars().all()]

    async def get_release_by_id(self, release_id: UUID) -> Optional[Release]:
        result = await self.db.execute(
            select(Release)
            .options(
                selectinload(Release.analysis),
                selectinload(Release.overrides),
                selectinload(Release.software),
            )
            .where(Release.id == release_id)
        )
        return result.scalar_one_or_none()

    async def get_staging_queue(self) -> List[Release]:
        """Get releases in staging workflow states (not just flagged)."""
        staging_states = [
            WorkflowState.STAGED,
            WorkflowState.UNDER_REVIEW,
        ]
        result = await self.db.execute(
            select(Release)
            .where(Release.workflow_state.in_(staging_states))
            .order_by(Release.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_discovered_releases(self) -> List[Release]:
        """Get newly discovered releases not yet staged."""
        result = await self.db.execute(
            select(Release)
            .where(Release.workflow_state == WorkflowState.DISCOVERED)
            .order_by(Release.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_approved_releases(self) -> List[Release]:
        """Get approved releases ready for action."""
        result = await self.db.execute(
            select(Release)
            .where(Release.workflow_state == WorkflowState.APPROVED)
            .order_by(Release.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_latest_downloaded_version(self, software_id: UUID) -> Optional[str]:
        """Return the version string of the most recently downloaded release for a software entry.

        Returns None if no release has reached DOWNLOADED state.
        """
        result = await self.db.execute(
            select(Release.version)
            .where(
                Release.software_id == software_id,
                Release.workflow_state == WorkflowState.DOWNLOADED,
            )
            .order_by(Release.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row

    async def get_all_releases(self, limit: int = 200) -> List[Release]:
        """Return all releases ordered by newest first, with software relationship loaded."""
        result = await self.db.execute(
            select(Release)
            .options(selectinload(Release.software))
            .order_by(Release.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_filtered_releases(
        self,
        page: int = 1,
        page_size: int = 50,
        software_id: Optional[UUID] = None,
        trust_status: Optional[str] = None,
        flag_status: Optional[str] = None,
        source_type: Optional[str] = None,
        workflow_state: Optional[str] = None,
    ) -> Tuple[List[Release], int]:
        """Return a filtered, paginated list of releases with total count.

        Returns a tuple of (releases, total_count).
        """
        conditions = []
        if software_id is not None:
            conditions.append(Release.software_id == software_id)
        if trust_status:
            try:
                conditions.append(Release.trust_status == TrustStatus(trust_status))
            except ValueError:
                pass
        if flag_status:
            try:
                conditions.append(Release.flag_status == FlagStatus(flag_status))
            except ValueError:
                pass
        if source_type:
            conditions.append(Release.source_type == source_type)
        if workflow_state:
            try:
                conditions.append(
                    Release.workflow_state == WorkflowState(workflow_state)
                )
            except ValueError:
                pass

        where_clause = and_(*conditions) if conditions else True

        # Total count
        count_result = await self.db.execute(
            select(func.count(Release.id)).where(where_clause)
        )
        total = count_result.scalar_one() or 0

        # Paginated rows
        offset = (page - 1) * page_size
        rows_result = await self.db.execute(
            select(Release)
            .options(selectinload(Release.software))
            .where(where_clause)
            .order_by(Release.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        releases = list(rows_result.scalars().all())
        return releases, total

    async def bulk_approve(self, release_ids: List[UUID], user: str = "admin") -> dict:
        """Approve multiple releases. Returns succeeded/failed lists."""
        succeeded = []
        failed = []
        for rid in release_ids:
            try:
                await self.approve_release(rid, approved_by=user)
                succeeded.append(str(rid))
            except (ValueError, Exception) as e:
                failed.append({"id": str(rid), "error": str(e)})
        return {"succeeded": succeeded, "failed": failed}

    async def bulk_reject(self, release_ids: List[UUID], user: str = "admin") -> dict:
        """Reject multiple releases. Returns succeeded/failed lists."""
        succeeded = []
        failed = []
        for rid in release_ids:
            try:
                release = await self.get_release_by_id(rid)
                if not release:
                    failed.append({"id": str(rid), "error": "Not found"})
                    continue
                # Determine valid rejection path
                allowed = VALID_TRANSITIONS.get(release.workflow_state, set())
                if WorkflowState.REJECTED not in allowed:
                    failed.append(
                        {
                            "id": str(rid),
                            "error": f"Cannot reject from state {release.workflow_state.value}",
                        }
                    )
                    continue
                await self.transition_state(
                    rid, WorkflowState.REJECTED, changed_by=user
                )
                succeeded.append(str(rid))
            except (ValueError, Exception) as e:
                failed.append({"id": str(rid), "error": str(e)})
        return {"succeeded": succeeded, "failed": failed}

    async def bulk_delete(self, release_ids: List[UUID]) -> dict:
        """Delete multiple releases. Returns succeeded/failed lists."""
        succeeded = []
        failed = []
        for rid in release_ids:
            try:
                deleted = await self.delete_release(rid)
                if deleted:
                    succeeded.append(str(rid))
                else:
                    failed.append({"id": str(rid), "error": "Not found"})
            except Exception as e:
                failed.append({"id": str(rid), "error": str(e)})
        return {"succeeded": succeeded, "failed": failed}

    async def delete_old_discovered(self, older_than_days: int) -> int:
        """Delete DISCOVERED releases older than the given number of days.

        Returns the number of releases deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        result = await self.db.execute(
            select(Release).where(
                Release.workflow_state == WorkflowState.DISCOVERED,
                Release.created_at < cutoff,
            )
        )
        releases = result.scalars().all()
        for release in releases:
            await self.db.delete(release)
        await self.db.commit()
        return len(releases)

    async def delete_old_rejected(self, older_than_days: int) -> int:
        """Delete REJECTED releases older than the given number of days.

        Returns the number of releases deleted.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        result = await self.db.execute(
            select(Release).where(
                Release.workflow_state == WorkflowState.REJECTED,
                Release.created_at < cutoff,
            )
        )
        releases = result.scalars().all()
        for release in releases:
            await self.db.delete(release)
        await self.db.commit()
        return len(releases)

    async def keep_latest_downloaded(self, software_id: UUID, keep_count: int) -> int:
        """Keep only the most recent `keep_count` DOWNLOADED releases for a software entry.

        Deletes all older DOWNLOADED releases beyond that count.
        Returns number deleted.
        """
        result = await self.db.execute(
            select(Release)
            .where(
                Release.software_id == software_id,
                Release.workflow_state == WorkflowState.DOWNLOADED,
            )
            .order_by(Release.created_at.desc())
        )
        releases = result.scalars().all()
        to_delete = releases[keep_count:]
        for release in to_delete:
            await self.db.delete(release)
        if to_delete:
            await self.db.commit()
        return len(to_delete)

    async def find_upgrades(self, software_id: UUID) -> List["ReleaseSearchResult"]:
        """Search for releases newer than the latest downloaded version.

        Returns search results whose parsed version is greater than the current
        downloaded version. Returns an empty list if no downloaded version exists
        or if no newer version is found.
        """
        current_version = await self.get_latest_downloaded_version(software_id)
        if not current_version:
            return []

        try:
            results = await self.search_releases(software_id, source_type="auto")
        except Exception:
            return []

        from softarr.utils.version import compare_versions

        upgrades = []
        for r in results:
            if r.version and compare_versions(r.version, current_version) > 0:
                upgrades.append(r)
        return upgrades

    async def get_by_version(
        self, software_id: UUID, version: str
    ) -> Optional[Release]:
        """Return an existing release for a software entry by version string, or None."""
        result = await self.db.execute(
            select(Release).where(
                Release.software_id == software_id,
                Release.version == version,
            )
        )
        return result.scalar_one_or_none()

    async def delete_release(self, release_id: UUID) -> bool:
        """Delete a release and its associated analysis/overrides. Returns True if deleted."""
        release = await self.get_release_by_id(release_id)
        if not release:
            return False
        await self.db.delete(release)
        await self.db.commit()
        return True

    async def get_release_stats(self) -> dict:
        """Return aggregate counts for the dashboard stat cards.

        Includes release counts (total, safe, flagged, downloaded) and
        software library counts (monitored, unmonitored, wanted).
        """
        total_result = await self.db.execute(select(func.count(Release.id)))
        total = total_result.scalar_one() or 0

        safe_result = await self.db.execute(
            select(func.count(Release.id)).where(Release.flag_status == FlagStatus.NONE)
        )
        safe = safe_result.scalar_one() or 0

        flagged_result = await self.db.execute(
            select(func.count(Release.id)).where(Release.flag_status != FlagStatus.NONE)
        )
        flagged = flagged_result.scalar_one() or 0

        downloaded_result = await self.db.execute(
            select(func.count(Release.id)).where(
                Release.workflow_state == WorkflowState.DOWNLOADED
            )
        )
        downloaded = downloaded_result.scalar_one() or 0

        # Software library counts
        monitored_result = await self.db.execute(
            select(func.count(Software.id)).where(
                Software.is_active == True,  # noqa: E712
                Software.monitored == True,  # noqa: E712
            )
        )
        monitored = monitored_result.scalar_one() or 0

        total_sw_result = await self.db.execute(
            select(func.count(Software.id)).where(Software.is_active == True)  # noqa: E712
        )
        total_sw = total_sw_result.scalar_one() or 0

        # Wanted = monitored software with no DOWNLOADED release
        all_monitored_result = await self.db.execute(
            select(Software.id).where(
                Software.is_active == True,  # noqa: E712
                Software.monitored == True,  # noqa: E712
            )
        )
        all_monitored_ids = [row[0] for row in all_monitored_result.all()]

        wanted = 0
        for sw_id in all_monitored_ids:
            downloaded_check = await self.db.execute(
                select(func.count(Release.id)).where(
                    Release.software_id == sw_id,
                    Release.workflow_state == WorkflowState.DOWNLOADED,
                )
            )
            if (downloaded_check.scalar_one() or 0) == 0:
                wanted += 1

        # Downloads in the last 7 days
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        downloaded_week_result = await self.db.execute(
            select(func.count(Release.id)).where(
                Release.workflow_state == WorkflowState.DOWNLOADED,
                Release.workflow_changed_at >= week_ago,
            )
        )
        downloaded_this_week = downloaded_week_result.scalar_one() or 0

        return {
            "total": total,
            "safe": safe,
            "flagged": flagged,
            "downloaded": downloaded,
            "downloaded_this_week": downloaded_this_week,
            "monitored": monitored,
            "total_software": total_sw,
            "wanted": wanted,
        }
