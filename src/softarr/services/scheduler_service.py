"""Scheduler service for periodic release checking.

Runs as a long-lived asyncio background task. On each tick it fetches all
active, monitored software entries and searches for new releases, storing
any that are not already in the database.

If a software entry has a download_profile with auto_approve_threshold > 0,
releases whose confidence_score meets or exceeds that threshold are
automatically approved after processing.

Configuration (softarr.ini):
  [scheduler]
  scheduler_enabled = true
  scheduler_interval_minutes = 60
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("softarr.scheduler")


class SchedulerService:
    def __init__(self, ini, db_factory):
        """
        Args:
            ini: IniSettingsManager instance.
            db_factory: Async context manager factory (e.g. AsyncSessionLocal) that
                        yields a database session.
        """
        self.ini = ini
        self.db_factory = db_factory
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Start the background scheduler loop."""
        if self._task and not self._task.done():
            logger.debug("Scheduler already running")
            return
        self._task = asyncio.ensure_future(self._loop())
        logger.info("Scheduler started")

    def stop(self) -> None:
        """Cancel the background scheduler loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Scheduler stopped")

    async def _loop(self) -> None:
        """Main scheduler loop: sleep then check all monitored software."""
        while True:
            interval = int(self.ini.get("scheduler_interval_minutes") or "60")
            await asyncio.sleep(interval * 60)
            try:
                await self._check_all_software()
            except Exception as exc:
                logger.error("Scheduler run failed: %s", exc)

    async def _check_all_software(self) -> dict:
        """Search for new releases for every active, monitored software entry.

        Returns a summary dict with counts of checked, new, and auto-approved.
        """
        from sqlalchemy import select, update

        from softarr.models.software import Software
        from softarr.services.release_service import ReleaseService
        from softarr.services.software_service import SoftwareService

        summary = {
            "checked": 0,
            "new": 0,
            "auto_approved": 0,
            "upgrades_found": 0,
            "errors": 0,
        }

        async with self.db_factory() as db:
            sw_service = SoftwareService(db)
            release_service = ReleaseService(db, self.ini)

            result = await db.execute(
                select(Software)
                .where(Software.is_active == True)  # noqa: E712
                .where(Software.monitored == True)  # noqa: E712
                .order_by(Software.canonical_name)
            )
            softwares = result.scalars().all()
            logger.info(
                "Scheduler: checking %d monitored software entries", len(softwares)
            )

            for software in softwares:
                summary["checked"] += 1
                try:
                    results = await release_service.search_releases(
                        software.id, source_type="auto"
                    )
                    new_count = 0
                    auto_approved = 0
                    for result_item in results:
                        existing = await release_service.get_by_version(
                            software.id, result_item.version
                        )
                        if not existing:
                            release = await release_service.process_and_store_release(
                                result_item, software.id
                            )
                            new_count += 1

                            # Auto-approve if confidence threshold is met
                            profile = getattr(software, "download_profile") or {}
                            threshold = float(
                                profile.get("auto_approve_threshold") or 0.0
                            )
                            if (
                                threshold > 0.0
                                and release.confidence_score >= threshold
                            ):
                                try:
                                    await release_service.approve_release(
                                        release.id, approved_by="scheduler"
                                    )
                                    auto_approved += 1
                                    logger.info(
                                        "Scheduler: auto-approved %s %s (score=%.2f >= threshold=%.2f)",
                                        software.canonical_name,
                                        release.version,
                                        release.confidence_score,
                                        threshold,
                                    )
                                except Exception as exc:
                                    logger.warning(
                                        "Scheduler: auto-approve failed for %s: %s",
                                        release.id,
                                        exc,
                                    )

                    summary["new"] += new_count
                    summary["auto_approved"] += auto_approved

                    if new_count > 0:
                        logger.info(
                            "Scheduler: found %d new release(s) for %s (%d auto-approved)",
                            new_count,
                            software.canonical_name,
                            auto_approved,
                        )

                    # Upgrade detection: check if any found release is newer
                    # than the latest downloaded version
                    try:
                        current_version = (
                            await release_service.get_latest_downloaded_version(
                                software.id
                            )
                        )
                        if current_version:
                            from softarr.services.release_rules_service import (
                                check_version_pin,
                            )
                            from softarr.utils.version import compare_versions

                            version_pin = getattr(software, "version_pin", None)

                            for result_item in results:
                                if (
                                    result_item.version
                                    and compare_versions(
                                        result_item.version, current_version
                                    )
                                    > 0
                                ):
                                    # Skip upgrade if blocked by version pin
                                    pin_ok, _pin_reason = check_version_pin(
                                        result_item.version, version_pin
                                    )
                                    if not pin_ok:
                                        logger.debug(
                                            "Scheduler: skipping upgrade for %s %s -- version pin: %s",
                                            software.canonical_name,
                                            result_item.version,
                                            _pin_reason,
                                        )
                                        continue

                                    summary["upgrades_found"] += 1
                                    logger.info(
                                        "Scheduler: upgrade available for %s: %s -> %s",
                                        software.canonical_name,
                                        current_version,
                                        result_item.version,
                                    )
                                    # Fire upgrade_available notification (fire-and-forget)
                                    try:
                                        import asyncio as _asyncio

                                        from softarr.services.notification_service import (
                                            NotificationService,
                                        )

                                        notif = NotificationService(self.ini)
                                        _asyncio.ensure_future(
                                            notif.notify(
                                                "upgrade_available",
                                                {
                                                    "name": software.canonical_name,
                                                    "current_version": current_version,
                                                    "version": result_item.version,
                                                },
                                            )
                                        )
                                    except Exception as notif_exc:
                                        logger.debug(
                                            "Scheduler: upgrade notification error: %s",
                                            notif_exc,
                                        )

                                    # Auto-queue upgrade if enabled and a download client is configured
                                    if (
                                        self.ini.get("auto_queue_upgrades") or "false"
                                    ).lower() == "true":
                                        active_client = (
                                            self.ini.get("active_download_client")
                                            or "sabnzbd"
                                        ).lower()
                                        if active_client == "qbittorrent":
                                            client_configured = bool(
                                                (self.ini.get("qbittorrent_url") or "")
                                                and (
                                                    self.ini.get("qbittorrent_username")
                                                    or ""
                                                )
                                            )
                                        else:
                                            client_configured = bool(
                                                self.ini.get("sabnzbd_url") or ""
                                            )
                                        if client_configured:
                                            try:
                                                from softarr.services.action_service import (
                                                    ActionService,
                                                )

                                                # Find the stored release for this upgrade
                                                upgrade_release = await release_service.get_by_version(
                                                    software.id, result_item.version
                                                )
                                                if (
                                                    upgrade_release
                                                    and upgrade_release.workflow_state.value
                                                    == "approved"
                                                ):
                                                    action_svc = ActionService(
                                                        db, self.ini
                                                    )
                                                    if active_client == "qbittorrent":
                                                        await (
                                                            action_svc.send_to_torrent(
                                                                upgrade_release.id,
                                                                user="scheduler",
                                                            )
                                                        )
                                                    else:
                                                        await (
                                                            action_svc.send_to_sabnzbd(
                                                                upgrade_release.id,
                                                                user="scheduler",
                                                            )
                                                        )
                                                    logger.info(
                                                        "Scheduler: auto-queued upgrade for %s %s (client: %s)",
                                                        software.canonical_name,
                                                        result_item.version,
                                                        active_client,
                                                    )
                                                    summary["auto_approved"] += 1
                                            except Exception as queue_exc:
                                                logger.warning(
                                                    "Scheduler: auto-queue upgrade failed for %s: %s",
                                                    software.canonical_name,
                                                    queue_exc,
                                                )

                                    break  # one notification per software per run
                    except Exception as upgrade_exc:
                        logger.debug(
                            "Scheduler: upgrade check failed for %s: %s",
                            software.canonical_name,
                            upgrade_exc,
                        )

                    # Update last_searched_at timestamp
                    await db.execute(
                        update(Software)
                        .where(Software.id == software.id)
                        .values(last_searched_at=datetime.now(timezone.utc))
                    )
                    await db.commit()

                except Exception as exc:
                    summary["errors"] += 1
                    logger.warning(
                        "Scheduler: error checking %s: %s",
                        software.canonical_name,
                        exc,
                    )

        return summary

    async def run_once(self) -> dict:
        """Trigger a single immediate check of all monitored software.

        Returns a summary dict with counts of checked, new, auto_approved, and errors.
        """
        try:
            summary = await self._check_all_software()
            return {
                "status": "ok",
                "message": "Scheduler run completed",
                **summary,
            }
        except Exception as exc:
            logger.error("Scheduler manual run failed: %s", exc)
            return {"status": "error", "message": str(exc)}
