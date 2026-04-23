"""Action execution service.

Handles the final "Action" step of the workflow:
  Stage -> Review -> Approve -> **Action**

Supported actions:
  - send_to_sabnzbd: Queue an NZB URL in SABnzbd
  - export_manifest: Generate a JSON manifest of the release metadata
  - (future) direct_download: HTTP download metadata handoff

Every action is explicitly audited. Actions are only permitted on
releases in the APPROVED workflow state.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from softarr.core.ini_settings import IniSettingsManager
from softarr.integrations.download_client import AbstractDownloadClient
from softarr.integrations.qbittorrent import (
    QBittorrentClient,
    QBittorrentConfig,
    QBittorrentError,
)
from softarr.integrations.sabnzbd import SABnzbdClient, SABnzbdConfig, SABnzbdError
from softarr.models.release import Release, WorkflowState
from softarr.services.audit_service import AuditService
from softarr.services.release_service import ReleaseService
from softarr.version import __version__ as _APP_VERSION

logger = logging.getLogger("softarr.actions")


class ActionError(Exception):
    pass


class ActionService:
    def __init__(self, db: AsyncSession, ini: IniSettingsManager):
        self.db = db
        self.ini = ini

    async def _get_approved_release(self, release_id: UUID) -> Release:
        """Load a release and verify it is in APPROVED state."""
        service = ReleaseService(self.db, self.ini)
        release = await service.get_release_by_id(release_id)
        if not release:
            raise ActionError(f"Release not found: {release_id}")
        if release.workflow_state != WorkflowState.APPROVED:
            raise ActionError(
                f"Release must be in APPROVED state to execute actions. "
                f"Current state: {release.workflow_state.value}"
            )
        return release

    def _get_download_client(self) -> AbstractDownloadClient:
        """Return the active download client based on INI configuration."""
        active = (self.ini.get("active_download_client") or "sabnzbd").lower()
        if active == "qbittorrent":
            return self._get_qbittorrent_client()
        return self._get_sabnzbd_client()

    def _get_qbittorrent_client(self) -> QBittorrentClient:
        """Build a qBittorrent client from INI settings."""
        url = self.ini.get("qbittorrent_url") or ""
        username = self.ini.get("qbittorrent_username") or ""
        password = self.ini.get("qbittorrent_password") or ""
        category = self.ini.get("qbittorrent_category") or "software"
        ssl_verify = (
            self.ini.get("qbittorrent_ssl_verify") or "true"
        ).lower() == "true"
        timeout = int(self.ini.get("qbittorrent_timeout") or "30")

        if not url or not username:
            raise ActionError(
                "qBittorrent is not configured. Set the URL and username in Settings."
            )

        config = QBittorrentConfig(
            url=url,
            username=username,
            password=password,
            category=category,
            ssl_verify=ssl_verify,
            timeout=timeout,
        )
        return QBittorrentClient(config)

    def _get_sabnzbd_client(self) -> SABnzbdClient:
        """Build a SABnzbd client from INI settings."""
        url = self.ini.get("sabnzbd_url") or ""
        api_key = self.ini.get("sabnzbd_api_key") or ""
        category = self.ini.get("sabnzbd_category") or "software"
        ssl_verify = (self.ini.get("sabnzbd_ssl_verify") or "true").lower() == "true"
        timeout = int(self.ini.get("sabnzbd_timeout") or "30")

        if not url or not api_key:
            raise ActionError(
                "SABnzbd is not configured. Set the URL and API key in Settings."
            )

        config = SABnzbdConfig(
            url=url,
            api_key=api_key,
            category=category,
            ssl_verify=ssl_verify,
            timeout=timeout,
        )
        return SABnzbdClient(config)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def send_to_sabnzbd(
        self,
        release_id: UUID,
        download_url: Optional[str] = None,
        category: Optional[str] = None,
        user: str = "system",
    ) -> Dict[str, Any]:
        """Send an approved release to SABnzbd.

        Uses the release's source_origin URL by default, or an explicit
        download_url if provided (e.g., a direct NZB link).
        """
        release = await self._get_approved_release(release_id)
        client = self._get_sabnzbd_client()

        url = download_url or release.source_origin
        if not url:
            raise ActionError(
                "No download URL available for this release. "
                "Provide a URL or ensure source_origin is set."
            )

        try:
            result = await client.send_url(
                url=url,
                name=f"{release.name} {release.version}",
                category=category or None,
            )
        except SABnzbdError as e:
            # Log the failure and re-raise
            audit = AuditService(self.db)
            await audit.log_action(
                "sabnzbd_send_failed",
                "release",
                release_id,
                user=user,
                details={"error": str(e), "url": url},
            )
            raise ActionError(f"SABnzbd send failed: {e}")

        # Store the SABnzbd job ID for reliable completion matching
        nzo_id = None
        if isinstance(result, dict):
            ids = result.get("ids") or []
            if ids:
                nzo_id = ids[0]
        if nzo_id:
            from sqlalchemy import update as sa_update

            from softarr.models.release import Release as ReleaseModel

            await self.db.execute(
                sa_update(ReleaseModel)
                .where(ReleaseModel.id == release_id)
                .values(download_client_id=nzo_id)
            )
            await self.db.commit()

        # Transition to QUEUED_FOR_DOWNLOAD
        release_service = ReleaseService(self.db, self.ini)
        try:
            await release_service.transition_state(
                release_id,
                WorkflowState.QUEUED_FOR_DOWNLOAD,
                changed_by=user,
            )
        except ValueError as exc:
            raise ActionError(str(exc)) from exc

        # Audit success
        audit = AuditService(self.db)
        await audit.log_action(
            "sabnzbd_send_success",
            "release",
            release_id,
            user=user,
            details={
                "url": url,
                "sabnzbd_response": str(result)[:500],
                "nzo_id": nzo_id,
            },
        )

        logger.info("Release %s queued in SABnzbd (nzo_id=%s)", release_id, nzo_id)

        if self.ini:
            import asyncio

            try:
                asyncio.create_task(self._notify_download_queued(release, user))
            except Exception as exc:
                logger.warning("Could not schedule download notification: %s", exc)

        return {
            "status": "queued",
            "release_id": str(release_id),
            "sabnzbd_response": result,
        }

    async def _notify_download_queued(self, release, user: str) -> None:
        """Send download-queued notification (fire-and-forget)."""
        try:
            from softarr.services.notification_service import NotificationService

            notif = NotificationService(self.ini)
            await notif.notify(
                "download_queued",
                {
                    "name": release.name,
                    "version": release.version,
                    "release_id": str(release.id),
                    "queued_by": user,
                },
            )
        except Exception as exc:
            logger.warning("Download notification failed: %s", exc)

    async def send_nzb_to_sabnzbd(
        self,
        release_id: UUID,
        nzb_bytes: bytes,
        filename: str,
        category: Optional[str] = None,
        user: str = "system",
    ) -> Dict[str, Any]:
        """Upload an NZB file directly to SABnzbd for an approved release.

        Use when the indexer provides an NZB file rather than a URL.
        Transitions the release to QUEUED_FOR_DOWNLOAD on success.
        """
        release = await self._get_approved_release(release_id)
        client = self._get_sabnzbd_client()

        try:
            result = await client.send_nzb_content(
                nzb_content=nzb_bytes,
                filename=filename,
                category=category or None,
            )
        except SABnzbdError as e:
            audit = AuditService(self.db)
            await audit.log_action(
                "sabnzbd_nzb_send_failed",
                "release",
                release_id,
                user=user,
                details={"error": str(e), "filename": filename},
            )
            raise ActionError(f"SABnzbd NZB upload failed: {e}")

        # Transition to QUEUED_FOR_DOWNLOAD
        release_service = ReleaseService(self.db, self.ini)
        try:
            await release_service.transition_state(
                release_id,
                WorkflowState.QUEUED_FOR_DOWNLOAD,
                changed_by=user,
            )
        except ValueError as exc:
            raise ActionError(str(exc)) from exc

        audit = AuditService(self.db)
        await audit.log_action(
            "sabnzbd_nzb_send_success",
            "release",
            release_id,
            user=user,
            details={
                "filename": filename,
                "sabnzbd_response": str(result)[:500],
            },
        )

        logger.info("Release %s NZB uploaded to SABnzbd (%s)", release_id, filename)

        if self.ini:
            import asyncio

            try:
                asyncio.create_task(self._notify_download_queued(release, user))
            except Exception as exc:
                logger.warning("Could not schedule download notification: %s", exc)

        return {
            "status": "queued",
            "release_id": str(release_id),
            "sabnzbd_response": result,
        }

    async def send_to_torrent(
        self,
        release_id: UUID,
        download_url: Optional[str] = None,
        category: Optional[str] = None,
        user: str = "system",
    ) -> Dict[str, Any]:
        """Send an approved release to qBittorrent.

        Uses the release's source_origin URL by default (should be a magnet
        link or .torrent URL). Transitions the release to QUEUED_FOR_DOWNLOAD
        on success and stores the torrent infohash as download_client_id.
        """
        release = await self._get_approved_release(release_id)
        client = self._get_qbittorrent_client()

        url = download_url or release.source_origin
        if not url:
            raise ActionError(
                "No download URL available for this release. "
                "Provide a magnet link or .torrent URL."
            )

        try:
            result = await client.send_url(
                url=url,
                name=f"{release.name} {release.version}",
                category=category or None,
            )
        except QBittorrentError as exc:
            audit = AuditService(self.db)
            await audit.log_action(
                "torrent_send_failed",
                "release",
                release_id,
                user=user,
                details={"error": str(exc), "url": url},
            )
            raise ActionError(f"qBittorrent send failed: {exc}")

        # Store the torrent hash (prefixed) as the job ID for completion matching
        job_id = None
        if isinstance(result, dict):
            ids = result.get("ids") or []
            if ids:
                job_id = ids[0]
        if job_id:
            from sqlalchemy import update as sa_update

            from softarr.models.release import Release as ReleaseModel

            await self.db.execute(
                sa_update(ReleaseModel)
                .where(ReleaseModel.id == release_id)
                .values(download_client_id=job_id)
            )
            await self.db.commit()

        release_service = ReleaseService(self.db, self.ini)
        try:
            await release_service.transition_state(
                release_id,
                WorkflowState.QUEUED_FOR_DOWNLOAD,
                changed_by=user,
            )
        except ValueError as exc:
            raise ActionError(str(exc)) from exc

        audit = AuditService(self.db)
        await audit.log_action(
            "torrent_send_success",
            "release",
            release_id,
            user=user,
            details={
                "url": url,
                "client_response": str(result)[:500],
                "job_id": job_id,
            },
        )

        logger.info("Release %s queued in qBittorrent (job_id=%s)", release_id, job_id)

        if self.ini:
            import asyncio

            try:
                asyncio.create_task(self._notify_download_queued(release, user))
            except Exception as exc:
                logger.warning("Could not schedule download notification: %s", exc)

        return {
            "status": "queued",
            "release_id": str(release_id),
            "client_response": result,
        }

    async def test_qbittorrent_connection(self) -> Dict[str, Any]:
        """Test the qBittorrent connection using current settings."""
        client = self._get_qbittorrent_client()
        return await client.test_connection()

    async def get_qbittorrent_queue(self) -> Dict[str, Any]:
        """Retrieve the current qBittorrent download queue."""
        client = self._get_qbittorrent_client()
        raw = await client.get_queue()
        slots = raw.get("queue", {}).get("slots", [])
        items = []
        for slot in slots:
            items.append(
                {
                    "hash": slot.get("hash", ""),
                    "filename": slot.get("filename", ""),
                    "percentage": slot.get("percentage", 0),
                    "size_mb": slot.get("size_mb", 0),
                    "status": slot.get("status", ""),
                }
            )
        return {"queue": items}

    async def test_sabnzbd_connection(self) -> Dict[str, Any]:
        """Test the SABnzbd connection using current settings."""
        client = self._get_sabnzbd_client()
        return await client.test_connection()

    async def get_sabnzbd_queue(self) -> Dict[str, Any]:
        """Retrieve the current SABnzbd download queue with progress information.

        Returns a normalised list of queue items with:
          - nzo_id: SABnzbd internal job ID
          - filename: display name of the job
          - percentage: integer percent complete (0-100)
          - mb_left: megabytes remaining
          - timeleft: estimated time remaining (SABnzbd HH:MM:SS string)
          - status: job status string (Downloading, Paused, etc.)
        """
        client = self._get_sabnzbd_client()
        raw = await client.get_queue()
        slots = raw.get("queue", {}).get("slots", [])
        items = []
        for slot in slots:
            mb_total = float(slot.get("mb", 0) or 0)
            mb_left = float(slot.get("mbleft", 0) or 0)
            percentage = 0
            if mb_total > 0:
                percentage = round((mb_total - mb_left) / mb_total * 100)
            items.append(
                {
                    "nzo_id": slot.get("nzo_id", ""),
                    "filename": slot.get("filename", slot.get("name", "")),
                    "percentage": percentage,
                    "mb_left": round(mb_left, 1),
                    "timeleft": slot.get("timeleft", ""),
                    "status": slot.get("status", ""),
                }
            )
        return {"queue": items}

    async def export_manifest(
        self, release_id: UUID, user: str = "system"
    ) -> Dict[str, Any]:
        """Export a JSON manifest of the release for external consumption.

        This is a non-destructive action that does not change workflow state.
        Useful for integration with other tools or manual download workflows.
        """
        release = await self._get_approved_release(release_id)

        manifest = {
            "softarr_version": _APP_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "release": {
                "id": str(release.id),
                "name": release.name,
                "version": release.version,
                "publisher": release.publisher,
                "source_type": release.source_type,
                "source_origin": release.source_origin,
                "supported_os": release.supported_os,
                "architecture": release.architecture,
                "confidence_score": release.confidence_score,
                "trust_status": release.trust_status.value,
                "flag_status": release.flag_status.value,
                "flag_reasons": release.flag_reasons,
                "workflow_state": release.workflow_state.value,
            },
        }

        if release.software:
            manifest["software"] = {
                "canonical_name": release.software.canonical_name,
                "expected_publisher": release.software.expected_publisher,
            }

        audit = AuditService(self.db)
        await audit.log_action(
            "export_manifest",
            "release",
            release_id,
            user=user,
        )

        return manifest
