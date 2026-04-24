import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.dependencies import require_admin
from softarr.core.database import get_db
from softarr.core.ini_settings import (
    SETTING_DEFINITIONS,
    IniSettingsManager,
    get_ini_settings,
)
from softarr.services.audit_service import AuditService
from softarr.services.settings_service import SettingsService
from softarr.version import __version__ as _APP_VERSION

router = APIRouter()


class SettingUpdate(BaseModel):
    key: str
    value: str


@router.get("/")
async def get_settings(
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Return all settings with secrets masked."""
    service = SettingsService(ini)
    settings = service.get_all_masked()
    return {"settings": settings}


@router.put("/")
async def update_setting(
    body: SettingUpdate,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update a single setting. Validates the key against known definitions."""
    if body.key not in SETTING_DEFINITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown setting key: {body.key}. "
            f"Valid keys: {list(SETTING_DEFINITIONS.keys())}",
        )

    service = SettingsService(ini)
    username = user.get("u", "admin")
    service.set(body.key, body.value)

    audit = AuditService(db)
    # Log the change but mask secret values
    defn = SETTING_DEFINITIONS[body.key]
    logged_value = "****" if defn.get("is_secret") else body.value
    await audit.log_action(
        "settings_change",
        "app_setting",
        None,
        user=username,
        details={"key": body.key, "value": logged_value},
    )

    return {"status": "updated", "key": body.key}


@router.get("/audit-log")
async def get_audit_log(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Return recent audit log entries."""
    audit = AuditService(db)
    logs = await audit.get_logs(limit=limit)
    return {
        "logs": [
            {
                "id": str(log.id),
                "action": log.action,
                "entity_type": log.entity_type,
                "entity_id": str(log.entity_id) if log.entity_id else None,
                "user": log.user,
                "details": log.details,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            }
            for log in logs
        ]
    }


@router.post("/scheduler/trigger")
async def trigger_scheduler(
    _user: dict = Depends(require_admin),
):
    """Manually trigger an immediate scheduler run (checks all software for new releases)."""
    from softarr.main import app

    scheduler = getattr(app.state, "scheduler", None)
    if not scheduler:
        raise HTTPException(status_code=503, detail="Scheduler is not running")

    result = await scheduler.run_once()
    return result


class NotificationTestRequest(BaseModel):
    channel: str


@router.post("/notifications/test")
async def test_notification_channel(
    body: NotificationTestRequest,
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Send a test notification through the specified channel."""
    from softarr.services.notification_service import NotificationService

    valid_channels = {"email", "discord", "http", "apprise"}
    if body.channel not in valid_channels:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown channel: {body.channel}. Valid: {sorted(valid_channels)}",
        )

    svc = NotificationService(ini)
    result = await svc.test_channel(body.channel)
    return result


@router.post("/backup/trigger")
async def trigger_backup(
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Manually trigger an immediate backup of the database and config."""
    from softarr.services.backup_service import BackupService

    svc = BackupService(ini)
    result = await svc.run_backup()
    return result


@router.get("/notification-history")
async def get_notification_history(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Return recent notification history entries."""
    from softarr.services.notification_service import NotificationService

    entries = await NotificationService.get_history(db, limit=limit)
    return {
        "history": [
            {
                "id": str(e.id),
                "event": e.event,
                "channel": e.channel,
                "success": e.success,
                "error_message": e.error_message,
                "sent_at": e.sent_at.isoformat() if e.sent_at else None,
            }
            for e in entries
        ]
    }


@router.get("/system-health")
async def get_system_health(
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Return live status of background tasks, adapter configuration, and security checks."""
    from softarr.main import app as _app

    scheduler_task = getattr(_app.state, "scheduler", None)
    retention_task = getattr(_app.state, "retention_task", None)
    hash_recheck_task = getattr(_app.state, "hash_recheck_task", None)
    backup_task = getattr(_app.state, "backup_task", None)

    service = SettingsService(ini)

    adapters = [
        {
            "name": "GitHub",
            "enabled": (service.get("github_adapter_enabled") or "true").lower()
            == "true",
        },
        {
            "name": "Usenet / Newznab",
            "enabled": (service.get("usenet_adapter_enabled") or "false").lower()
            == "true",
        },
        {
            "name": "Torznab / Torrent",
            "enabled": (service.get("torznab_adapter_enabled") or "false").lower()
            == "true",
        },
        {
            "name": "VirusTotal",
            "enabled": (service.get("virustotal_enabled") or "false").lower() == "true",
        },
        {
            "name": "NSRL",
            "enabled": (service.get("nsrl_enabled") or "false").lower() == "true",
        },
    ]

    # Audit log count
    audit_svc = AuditService(db)
    audit_log_count = await audit_svc.count_logs()

    # Download client health check -- detects active client (SABnzbd or qBittorrent)
    from softarr.services.action_service import ActionService

    active_client = (service.get("active_download_client") or "sabnzbd").lower()
    if active_client == "qbittorrent":
        qbt_url = service.get("qbittorrent_url") or ""
        qbt_user = service.get("qbittorrent_username") or ""
        download_client: dict = {
            "type": "qbittorrent",
            "configured": bool(qbt_url and qbt_user),
            "url": qbt_url,
            "reachable": None,
            "version": None,
            "queue_depth": None,
        }
        if download_client["configured"]:
            try:
                action_svc = ActionService(db, ini)
                conn = await action_svc.test_qbittorrent_connection()
                queue_data = await action_svc.get_qbittorrent_queue()
                download_client["reachable"] = True
                download_client["version"] = conn.get("version")
                download_client["queue_depth"] = len(queue_data.get("queue", []))
            except Exception:
                download_client["reachable"] = False
    else:
        sabnzbd_url = service.get("sabnzbd_url") or ""
        sabnzbd_api_key = service.get("sabnzbd_api_key") or ""
        download_client = {
            "type": "sabnzbd",
            "configured": bool(sabnzbd_url and sabnzbd_api_key),
            "url": sabnzbd_url,
            "reachable": None,
            "version": None,
            "queue_depth": None,
        }
        if download_client["configured"]:
            try:
                action_svc = ActionService(db, ini)
                queue_data = await action_svc.get_sabnzbd_queue()
                slots = queue_data.get("queue", {}).get("slots", [])
                download_client["reachable"] = True
                download_client["queue_depth"] = len(slots)
            except Exception:
                download_client["reachable"] = False

    # ------------------------------------------------------------------
    # Security checks
    # ------------------------------------------------------------------

    from uuid import UUID as _UUID

    from softarr.auth.service import AuthService as _AuthService

    # 2FA check -- look up the current user's totp_enabled from the DB
    current_db_user = await _AuthService(db).get_user_by_id(_UUID(user["uid"]))
    totp_ok = bool(current_db_user and current_db_user.totp_enabled)

    # Password policy -- all four controls must be active
    pw_uppercase = (ini.get("password_require_uppercase") or "false").lower() == "true"
    pw_numbers = (ini.get("password_require_numbers") or "false").lower() == "true"
    pw_special = (ini.get("password_require_special") or "false").lower() == "true"
    pw_max_age = int(ini.get("password_max_age_days") or "0")
    password_policy_ok = pw_uppercase and pw_numbers and pw_special and pw_max_age > 0

    # Version check -- query GitHub releases API
    version_ok = False
    version_latest: str | None = None
    version_error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as _client:
            resp = await _client.get(
                "https://api.github.com/repos/arrsome/softarr/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code == 200:
                from softarr.utils.version import compare_versions

                data = resp.json()
                raw_tag = data.get("tag_name") or ""
                version_latest = raw_tag.lstrip("v") or None
                version_ok = bool(
                    version_latest
                    and compare_versions(_APP_VERSION, version_latest) >= 0
                )
            else:
                version_error = f"GitHub API returned {resp.status_code}"
    except Exception as exc:
        version_error = str(exc)

    # Anti-piracy filter and NSRL
    antipiracy_ok = (ini.get("antipiracy_enabled") or "true").lower() == "true"
    nsrl_ok = (ini.get("nsrl_enabled") or "true").lower() == "true"

    security = {
        "totp_enabled": totp_ok,
        "password_policy_ok": password_policy_ok,
        "password_policy_detail": {
            "require_uppercase": pw_uppercase,
            "require_numbers": pw_numbers,
            "require_special": pw_special,
            "max_age_days": pw_max_age,
        },
        "version_ok": version_ok,
        "version_current": _APP_VERSION,
        "version_latest": version_latest,
        "version_error": version_error,
        "antipiracy_enabled": antipiracy_ok,
        "nsrl_enabled": nsrl_ok,
    }

    return {
        "tasks": {
            "scheduler": scheduler_task is not None
            and getattr(scheduler_task, "_task", None) is not None
            and not (
                getattr(scheduler_task, "_task", None)
                and getattr(scheduler_task._task, "done", lambda: True)()
            ),
            "retention": retention_task is not None and not retention_task.done(),
            "hash_recheck": hash_recheck_task is not None
            and not hash_recheck_task.done(),
            "backup": backup_task is not None and not backup_task.done(),
        },
        "adapters": adapters,
        "audit_log_count": audit_log_count,
        "audit_retention_days": int(ini.get("audit_retention_days") or "365"),
        "download_client": download_client,
        "security": security,
    }


class SecurityPresetRequest(BaseModel):
    preset: str  # "nanny" | "adult" | "custom"


@router.post("/security-preset")
async def apply_security_preset(
    body: SecurityPresetRequest,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Apply a security preset bundle of settings.

    ``nanny``  -- enables all security controls (strict mode).
    ``adult``  -- disables hash verification, password max age, and anti-piracy.
    ``custom`` -- no-op; confirms that settings are in a custom (manual) state.

    All changes are applied via the INI settings manager and audit-logged.
    """
    if body.preset not in ("nanny", "adult", "custom"):
        raise HTTPException(
            status_code=400,
            detail="preset must be one of: nanny, adult, custom",
        )

    if body.preset == "nanny":
        changes: Dict[str, str] = {
            "totp_required": "true",
            "password_require_uppercase": "true",
            "password_require_numbers": "true",
            "password_require_special": "true",
            "password_max_age_days": "90",
            "antipiracy_enabled": "true",
            "nsrl_enabled": "true",
            "hash_verification_enabled": "true",
        }
    elif body.preset == "adult":
        changes = {
            "hash_verification_enabled": "false",
            "password_max_age_days": "0",
            "antipiracy_enabled": "false",
        }
    else:
        changes = {}

    for key, value in changes.items():
        ini.set(key, value)

    audit = AuditService(db)
    await audit.log_action(
        "security_preset_applied",
        "settings",
        None,
        user=user.get("u", "admin"),
        details={"preset": body.preset, "changes": list(changes.keys())},
    )

    return {"preset": body.preset, "applied": list(changes.keys())}


@router.get("/audit-log/export")
async def export_audit_log_csv(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """Export the full audit log as a CSV file download."""
    audit = AuditService(db)
    logs = await audit.get_logs(limit=10000)

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "timestamp",
            "action",
            "entity_type",
            "entity_id",
            "user",
            "details",
        ],
    )
    writer.writeheader()
    for log in logs:
        writer.writerow(
            {
                "id": str(log.id),
                "timestamp": log.timestamp.isoformat() if log.timestamp else "",
                "action": log.action,
                "entity_type": log.entity_type or "",
                "entity_id": str(log.entity_id) if log.entity_id else "",
                "user": log.user or "",
                "details": str(log.details) if log.details else "",
            }
        )

    csv_bytes = output.getvalue().encode("utf-8")
    return StreamingResponse(
        iter([csv_bytes]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=softarr-audit-log.csv"},
    )


# ---------------------------------------------------------------------------
# Config export / import (TBI-13)
# ---------------------------------------------------------------------------


class ConfigImportBody(BaseModel):
    settings: Dict[str, Any]


@router.get("/export", summary="Export configuration (secrets masked)")
async def export_config(
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Return all current settings as a JSON snapshot.

    Secret values are masked (replaced with '****') so the export is safe
    to share. Masked values are ignored by the import endpoint.
    """
    service = SettingsService(ini)
    return {
        "version": _APP_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "settings": service.get_all_masked(),
    }


@router.post("/import", summary="Import configuration from snapshot")
async def import_config(
    body: ConfigImportBody,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Apply settings from a previously exported config snapshot.

    Rules:
    - Unknown keys (not in SETTING_DEFINITIONS) are skipped.
    - Masked values (starting with '****') are skipped to avoid overwriting
      real secrets with placeholder text.
    - Applied keys are audit-logged (count only, no values).
    """
    applied = 0
    skipped = 0
    for key, value in body.settings.items():
        if key not in SETTING_DEFINITIONS:
            skipped += 1
            continue
        str_value = str(value) if value is not None else ""
        if str_value.startswith("****"):
            skipped += 1
            continue
        ini.set(key, str_value)
        applied += 1

    audit = AuditService(db)
    await audit.log_action(
        "config_import",
        "settings",
        None,
        user=user.get("u", "admin"),
        details={"applied": applied, "skipped": skipped},
    )
    return {"applied": applied, "skipped": skipped}
