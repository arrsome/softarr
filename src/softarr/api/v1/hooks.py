"""Inbound webhook receiver.

Accepts callbacks from download clients (SABnzbd post-processing scripts,
etc.) to automatically transition releases to the DOWNLOADED state when a
download completes.

Supported client types:
  - sabnzbd: SABnzbd post-processing script notification

Security:
  An optional shared secret can be configured in softarr.ini as
  ``sabnzbd_webhook_secret``. When set, every request must include an
  ``X-Webhook-Secret`` header that matches. When not set, the endpoint
  accepts all requests (suitable for trusted internal networks only).

SABnzbd post-processing script example::

    curl -s -X POST \\
        http://softarr:8000/api/v1/hooks/sabnzbd \\
        -H "Content-Type: application/json" \\
        -H "X-Webhook-Secret: your-secret" \\
        -d '{"nzo_id": "SABnzbd_nzo_abc123", "status": "Completed", "name": "LibreOffice 25.8.1"}'
"""

import asyncio
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.core.database import get_db
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.middleware.rate_limit import limiter
from softarr.models.release import Release, WorkflowState
from softarr.services.audit_service import AuditService
from softarr.services.release_service import ReleaseService

logger = logging.getLogger("softarr.hooks")

router = APIRouter()

SUPPORTED_CLIENTS = {"sabnzbd", "qbittorrent"}
_FAILURE_STATUSES = {"failed", "failure", "aborted", "bad"}
_SUCCESS_STATUSES = {"completed", "complete", "success"}


class SABnzbdHookPayload(BaseModel):
    """Payload sent by a SABnzbd post-processing script."""

    nzo_id: str = ""  # SABnzbd internal job ID
    status: str = ""  # "Completed", "Failed", etc.
    name: str = ""  # Job display name
    category: str = ""  # SABnzbd category
    release_id: str = ""  # Optional: Softarr release UUID (for exact matching)


class QBittorrentHookPayload(BaseModel):
    """Payload for a qBittorrent completion webhook.

    Since qBittorrent has no native outbound webhook, this endpoint is
    intended for use with qBittorrent's "Run Program on Torrent Completion"
    feature or the Softarr background polling task.

    To use with qBittorrent's run-on-completion feature, add the following
    to Options -> Downloads -> Run Program on Torrent Completion:

        curl -s -X POST http://softarr:8000/api/v1/hooks/qbittorrent
          -H "Content-Type: application/json"
          -H "X-Webhook-Secret: your-secret"
          -d '{"hash": "%I", "name": "%N", "status": "completed", "category": "%L"}'

    (%I = infohash, %N = name, %L = label/category -- qBittorrent format strings)
    """

    hash: str = ""  # Torrent infohash (40-char hex, without 'qbt:' prefix)
    name: str = ""  # Display name for fallback matching
    status: str = ""  # "completed" | "failed"
    category: str = ""
    release_id: str = ""  # Optional: explicit Softarr release UUID


def _verify_webhook_secret(
    request: Request,
    ini: IniSettingsManager,
    secret_key: str = "sabnzbd_webhook_secret",
) -> None:
    """Validate X-Webhook-Secret header if a secret is configured.

    The secret_key parameter selects which INI key holds the expected secret,
    allowing SABnzbd and qBittorrent hooks to use independent secrets.
    """
    stored_secret = ini.get(secret_key) or ""
    if not stored_secret:
        return  # No secret configured -- accept all requests from trusted networks
    provided = request.headers.get("x-webhook-secret", "")
    if not provided or not secrets.compare_digest(provided, stored_secret):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")


async def _find_release(
    payload: SABnzbdHookPayload,
    release_service: ReleaseService,
    db: AsyncSession,
) -> Release | None:
    """Attempt to match the hook payload to a release.

    Matching priority:
      1. Exact UUID match via payload.release_id
      2. Exact download_client_id match via payload.nzo_id (reliable)
      3. Name substring match in QUEUED_FOR_DOWNLOAD releases (fallback)
    """
    # 1. Exact UUID
    if payload.release_id:
        try:
            from uuid import UUID

            r = await release_service.get_release_by_id(UUID(payload.release_id))
            if r:
                return r
        except ValueError, Exception:
            pass

    # 2. Exact download_client_id (nzo_id stored when we submitted the job)
    if payload.nzo_id:
        result = await db.execute(
            select(Release).where(Release.download_client_id == payload.nzo_id)
        )
        r = result.scalar_one_or_none()
        if r:
            return r

    # 3. Name substring fallback (unreliable -- warn)
    if payload.name:
        result = await db.execute(
            select(Release).where(
                Release.workflow_state == WorkflowState.QUEUED_FOR_DOWNLOAD
            )
        )
        queued = result.scalars().all()
        for r in queued:
            if payload.name.lower() in (r.name or "").lower():
                logger.warning(
                    "Hook: matched release by name substring (nzo_id not stored). "
                    "Upgrade Softarr for reliable matching. nzo_id=%r name=%r release=%s",
                    payload.nzo_id,
                    payload.name,
                    r.id,
                )
                return r

    return None


async def _handle_qbittorrent_hook(
    request: Request,
    db: AsyncSession,
    ini: IniSettingsManager,
) -> dict:
    """Handle a completion webhook from qBittorrent (or its polling proxy).

    Matches the release by torrent hash stored as 'qbt:<hash>' in
    Release.download_client_id, then by explicit release_id, then by name.
    """
    _verify_webhook_secret(request, ini, secret_key="qbittorrent_webhook_secret")

    try:
        payload = QBittorrentHookPayload(**await request.json())
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {exc}")

    status_lower = payload.status.lower()
    logger.info(
        "Received qbittorrent webhook: hash=%r status=%r name=%r",
        payload.hash,
        payload.status,
        payload.name,
    )

    is_success = status_lower in _SUCCESS_STATUSES
    is_failure = status_lower in _FAILURE_STATUSES

    if not is_success and not is_failure:
        return {
            "status": "ignored",
            "reason": f"non-completion status: {payload.status}",
        }

    from softarr.integrations.qbittorrent import QBIT_HASH_PREFIX

    release_service = ReleaseService(db, ini)
    release = None

    # 1. Match by explicit release_id
    if payload.release_id:
        try:
            from uuid import UUID as _UUID

            release = await release_service.get_release_by_id(_UUID(payload.release_id))
        except ValueError, Exception:
            pass

    # 2. Match by prefixed hash stored in download_client_id
    if not release and payload.hash:
        result = await db.execute(
            select(Release).where(
                Release.download_client_id == f"{QBIT_HASH_PREFIX}{payload.hash}"
            )
        )
        release = result.scalar_one_or_none()

    if not release:
        logger.warning(
            "qBittorrent hook: could not match hash=%r name=%r to a release",
            payload.hash,
            payload.name,
        )
        return {"status": "unmatched", "hash": payload.hash}

    if release.workflow_state != WorkflowState.QUEUED_FOR_DOWNLOAD:
        return {
            "status": "skipped",
            "reason": f"release is in state {release.workflow_state.value}, not queued",
        }

    target_state = (
        WorkflowState.DOWNLOADED if is_success else WorkflowState.DOWNLOAD_FAILED
    )

    try:
        await release_service.transition_state(
            release.id, target_state, changed_by="hook:qbittorrent"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    audit = AuditService(db)
    await audit.log_action(
        "download_complete" if is_success else "download_failed",
        "release",
        release.id,
        user="hook:qbittorrent",
        details={
            "hash": payload.hash,
            "client": "qbittorrent",
            "status": payload.status,
        },
    )

    if is_success:
        try:
            from softarr.services.notification_service import NotificationService

            notif = NotificationService(ini)
            asyncio.create_task(
                notif.notify(
                    "download_complete",
                    {
                        "name": release.name,
                        "version": release.version,
                        "release_id": str(release.id),
                        "client": "qbittorrent",
                    },
                )
            )
        except Exception as exc:
            logger.warning("qBittorrent hook: notification error: %s", exc)

    logger.info(
        "qBittorrent hook: transitioned release %s (%s %s) to %s",
        release.id,
        release.name,
        release.version,
        target_state.value,
    )

    return {
        "status": "ok",
        "release_id": str(release.id),
        "release_name": release.name,
        "release_version": release.version,
        "new_state": target_state.value,
    }


@router.post("/{client_type}")
@limiter.limit("20/minute")
async def receive_hook(
    client_type: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
):
    """Receive a post-processing callback from a download client.

    Supported client_type values: sabnzbd

    On a Completed status the endpoint finds the matching release by nzo_id
    or release_id and transitions it to DOWNLOADED.
    On a Failed/Aborted status it transitions to DOWNLOAD_FAILED.
    """
    if client_type not in SUPPORTED_CLIENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown client type: {client_type!r}. Supported: {sorted(SUPPORTED_CLIENTS)}",
        )

    if client_type == "qbittorrent":
        return await _handle_qbittorrent_hook(request, db, ini)

    _verify_webhook_secret(request, ini)

    try:
        payload = SABnzbdHookPayload(**await request.json())
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid payload: {exc}")

    status_lower = payload.status.lower()
    logger.info(
        "Received %s webhook: nzo_id=%r status=%r name=%r",
        client_type,
        payload.nzo_id,
        payload.status,
        payload.name,
    )

    is_success = status_lower in _SUCCESS_STATUSES
    is_failure = status_lower in _FAILURE_STATUSES

    if not is_success and not is_failure:
        return {
            "status": "ignored",
            "reason": f"non-completion status: {payload.status}",
        }

    release_service = ReleaseService(db, ini)
    release = await _find_release(payload, release_service, db)

    if not release:
        logger.warning(
            "Hook: could not match SABnzbd job nzo_id=%r name=%r to a release",
            payload.nzo_id,
            payload.name,
        )
        return {"status": "unmatched", "nzo_id": payload.nzo_id}

    if release.workflow_state != WorkflowState.QUEUED_FOR_DOWNLOAD:
        return {
            "status": "skipped",
            "reason": f"release is in state {release.workflow_state.value}, not queued",
        }

    target_state = (
        WorkflowState.DOWNLOADED if is_success else WorkflowState.DOWNLOAD_FAILED
    )

    try:
        await release_service.transition_state(
            release.id,
            target_state,
            changed_by=f"hook:{client_type}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    audit = AuditService(db)
    await audit.log_action(
        "download_complete" if is_success else "download_failed",
        "release",
        release.id,
        user=f"hook:{client_type}",
        details={
            "nzo_id": payload.nzo_id,
            "client": client_type,
            "status": payload.status,
        },
    )

    if is_success:
        # Fire download_complete notification (fire-and-forget)
        try:
            from softarr.services.notification_service import NotificationService

            notif = NotificationService(ini)
            asyncio.create_task(
                notif.notify(
                    "download_complete",
                    {
                        "name": release.name,
                        "version": release.version,
                        "release_id": str(release.id),
                        "client": client_type,
                    },
                )
            )
        except Exception as exc:
            logger.warning("Hook: notification error: %s", exc)

        # Trigger post-download hash verification (fire-and-forget)
        vt_enabled = (ini.get("virustotal_enabled") or "false").lower() == "true"
        nsrl_enabled = (ini.get("nsrl_enabled") or "false").lower() == "true"
        if vt_enabled or nsrl_enabled:
            try:
                from softarr.services.hash_intelligence_service import (
                    HashIntelligenceService,
                )

                hash_svc = HashIntelligenceService(db, ini)
                asyncio.create_task(hash_svc.recheck_unknown())
            except Exception as exc:
                logger.debug("Hook: hash recheck scheduling failed: %s", exc)

    logger.info(
        "Hook: transitioned release %s (%s %s) to %s",
        release.id,
        release.name,
        release.version,
        target_state.value,
    )

    return {
        "status": "ok",
        "release_id": str(release.id),
        "release_name": release.name,
        "release_version": release.version,
        "new_state": target_state.value,
    }
