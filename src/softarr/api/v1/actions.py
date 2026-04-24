from typing import Optional
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.dependencies import require_admin
from softarr.core.database import get_db
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.middleware.rate_limit import limiter
from softarr.schemas.integrations import (
    ActionRequest,
    ActiveClientUpdate,
    QBittorrentConfigUpdate,
    QBittorrentSendRequest,
    SABnzbdConfigUpdate,
    SABnzbdSendRequest,
)
from softarr.integrations.qbittorrent import QBittorrentError
from softarr.integrations.sabnzbd import SABnzbdError
from softarr.services.action_service import ActionError, ActionService
from softarr.services.audit_service import AuditService

router = APIRouter()


# ---------------------------------------------------------------------------
# SABnzbd configuration
# ---------------------------------------------------------------------------


@router.put("/sabnzbd/config")
async def update_sabnzbd_config(
    body: SABnzbdConfigUpdate,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update SABnzbd connection settings (persisted to softarr.ini)."""
    username = user.get("u", "admin")

    ini.set("sabnzbd_url", body.url)
    ini.set("sabnzbd_api_key", body.api_key)
    ini.set("sabnzbd_category", body.category)
    ini.set("sabnzbd_ssl_verify", str(body.ssl_verify).lower())
    ini.set("sabnzbd_timeout", str(body.timeout))

    # Strip any embedded credentials from the URL before logging
    try:
        _parsed = urlparse(body.url)
        safe_url = (
            _parsed._replace(netloc=_parsed.hostname or "").geturl()
            if _parsed.password or _parsed.username
            else body.url
        )
    except Exception:
        safe_url = body.url

    audit = AuditService(db)
    await audit.log_action(
        "sabnzbd_config_updated",
        "app_setting",
        None,
        user=username,
        details={"url": safe_url, "category": body.category},
    )

    return {"status": "updated"}


@router.get("/sabnzbd/queue")
async def get_sabnzbd_queue(
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Get the current SABnzbd download queue with progress information."""
    action = ActionService(db, ini)
    try:
        return await action.get_sabnzbd_queue()
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sabnzbd/test")
async def test_sabnzbd_connection(
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Test the SABnzbd connection with current settings."""
    action = ActionService(db, ini)
    try:
        result = await action.test_sabnzbd_connection()
        return {"status": "ok", **result}
    except (ActionError, SABnzbdError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Release actions
# ---------------------------------------------------------------------------


@router.post("/sabnzbd/send")
@limiter.limit("5/minute")
async def send_to_sabnzbd(
    request: Request,
    body: SABnzbdSendRequest,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Send an approved release to SABnzbd for download.

    Only works on releases in APPROVED workflow state.
    Transitions the release to QUEUED_FOR_DOWNLOAD on success.
    Rate limited to 5 requests per minute to prevent duplicate submissions.
    """
    action = ActionService(db, ini)
    try:
        result = await action.send_to_sabnzbd(
            release_id=UUID(body.release_id),
            download_url=body.download_url,
            category=body.category,
            user=user.get("u", "admin"),
        )
        return result
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/sabnzbd/send-nzb")
@limiter.limit("5/minute")
async def send_nzb_to_sabnzbd(
    request: Request,
    release_id: str,
    nzb_file: UploadFile,
    category: Optional[str] = Form(default=None),
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Upload an NZB file directly to SABnzbd for an approved release.

    Use this when the indexer provides an NZB file rather than a URL.
    Only works on releases in APPROVED workflow state.
    Rate limited to 5 requests per minute to prevent duplicate submissions.
    """
    _MAX_NZB_SIZE = 10 * 1024 * 1024  # 10 MB
    action = ActionService(db, ini)
    try:
        nzb_bytes = await nzb_file.read(_MAX_NZB_SIZE + 1)
        if len(nzb_bytes) > _MAX_NZB_SIZE:
            raise HTTPException(
                status_code=413, detail="NZB file too large (max 10 MB)"
            )
        result = await action.send_nzb_to_sabnzbd(
            release_id=UUID(release_id),
            nzb_bytes=nzb_bytes,
            filename=nzb_file.filename or f"{release_id}.nzb",
            category=category,
            user=user.get("u", "admin"),
        )
        return result
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Active download client selector
# ---------------------------------------------------------------------------


@router.put("/active-client")
async def set_active_download_client(
    body: ActiveClientUpdate,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Set the active download client (sabnzbd or qbittorrent).

    Persisted to softarr.ini. Takes effect immediately for new send requests.
    """
    ini.set("active_download_client", body.client)

    audit = AuditService(db)
    await audit.log_action(
        "active_download_client_changed",
        "app_setting",
        None,
        user=user.get("u", "admin"),
        details={"client": body.client},
    )
    return {"status": "updated", "active_client": body.client}


# ---------------------------------------------------------------------------
# qBittorrent configuration and actions
# ---------------------------------------------------------------------------


@router.put("/qbittorrent/config")
async def update_qbittorrent_config(
    body: QBittorrentConfigUpdate,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update qBittorrent connection settings (persisted to softarr.ini)."""
    ini.set("qbittorrent_url", body.url)
    ini.set("qbittorrent_username", body.username)
    ini.set("qbittorrent_password", body.password)
    ini.set("qbittorrent_category", body.category)
    ini.set("qbittorrent_ssl_verify", str(body.ssl_verify).lower())
    ini.set("qbittorrent_timeout", str(body.timeout))

    try:
        _parsed = urlparse(body.url)
        safe_url = (
            _parsed._replace(netloc=_parsed.hostname or "").geturl()
            if _parsed.password or _parsed.username
            else body.url
        )
    except Exception:
        safe_url = body.url

    audit = AuditService(db)
    await audit.log_action(
        "qbittorrent_config_updated",
        "app_setting",
        None,
        user=user.get("u", "admin"),
        details={"url": safe_url, "category": body.category},
    )
    return {"status": "updated"}


@router.post("/qbittorrent/test")
async def test_qbittorrent_connection(
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Test the qBittorrent connection with current settings."""
    action = ActionService(db, ini)
    try:
        result = await action.test_qbittorrent_connection()
        return {"status": "ok", **result}
    except (ActionError, QBittorrentError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/qbittorrent/send")
@limiter.limit("5/minute")
async def send_to_qbittorrent(
    request: Request,
    body: QBittorrentSendRequest,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Send an approved release to qBittorrent for download.

    The release's source_origin should be a magnet link or .torrent URL.
    Only works on releases in APPROVED workflow state.
    Transitions the release to QUEUED_FOR_DOWNLOAD on success.
    Rate limited to 5 requests per minute to prevent duplicate submissions.
    """
    action = ActionService(db, ini)
    try:
        result = await action.send_to_torrent(
            release_id=UUID(body.release_id),
            download_url=body.download_url,
            category=body.category,
            user=user.get("u", "admin"),
        )
        return result
    except ActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/qbittorrent/queue")
async def get_qbittorrent_queue(
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Get the current qBittorrent download queue."""
    action = ActionService(db, ini)
    try:
        return await action.get_qbittorrent_queue()
    except ActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/virustotal/test")
async def test_virustotal_connection(
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Test the VirusTotal API key by making a minimal authenticated request.

    Uses the SHA-256 of an empty string as the test hash. A 200 or 404 response
    from VirusTotal indicates the API key is valid; 401 indicates an invalid key.
    """
    import httpx

    api_key = ini.get("virustotal_api_key") or ""
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="No VirusTotal API key configured. Save a key first.",
        )

    # SHA-256 of empty string -- VT will return 404 (not found) for a valid key,
    # or 401 for an invalid key. Either way the key is tested without side effects.
    test_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    url = f"https://www.virustotal.com/api/v3/files/{test_hash}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={"x-apikey": api_key})
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach VirusTotal: {e}",
        )

    if resp.status_code == 401:
        raise HTTPException(
            status_code=400,
            detail="VirusTotal rejected the API key (HTTP 401). Check that the key is correct.",
        )
    if resp.status_code not in (200, 404):
        raise HTTPException(
            status_code=502,
            detail=f"Unexpected response from VirusTotal: HTTP {resp.status_code}",
        )

    return {"status": "ok", "message": "VirusTotal API key is valid and reachable."}


# ---------------------------------------------------------------------------
# Grab -- approve + queue in one step (TBI-09)
# ---------------------------------------------------------------------------


@router.post("/grab/{release_id}")
@limiter.limit("5/minute")
async def grab_release(
    request: Request,
    release_id: UUID,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Approve a release and immediately queue it to the active download client.

    Combines the approve + send steps into a single action. If the release is
    not yet in APPROVED state, it is approved first (provided it is in a
    transitionable state). The release is then sent to whichever download
    client is currently active (SABnzbd or qBittorrent).

    Rate limited to 5 requests per minute to prevent duplicate submissions.
    """
    from softarr.models.release import WorkflowState
    from softarr.services.release_service import ReleaseService

    username = user.get("u", "admin")
    release_svc = ReleaseService(db, ini)
    action_svc = ActionService(db, ini)

    release = await release_svc.get_release_by_id(release_id)
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    # Approve first if not already approved
    if release.workflow_state != WorkflowState.APPROVED:
        try:
            await release_svc.approve_release(release_id, approved_by=username)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # Send to whichever client is active
    active = (ini.get("active_download_client") or "sabnzbd").lower()
    try:
        if active == "qbittorrent":
            result = await action_svc.send_to_torrent(release_id, user=username)
        else:
            result = await action_svc.send_to_sabnzbd(release_id, user=username)
    except ActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    audit = AuditService(db)
    await audit.log_action(
        "grab",
        "release",
        release_id,
        user=username,
        details={"client": active},
    )
    return result


@router.post("/export")
async def export_manifest(
    body: ActionRequest,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Export a JSON manifest for an approved release.

    Non-destructive; does not change workflow state.
    """
    if body.action != "export_manifest":
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")

    action = ActionService(db, ini)
    try:
        manifest = await action.export_manifest(
            release_id=UUID(body.release_id),
            user=user.get("u", "admin"),
        )
        return manifest
    except ActionError as e:
        raise HTTPException(status_code=400, detail=str(e))
