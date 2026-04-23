from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.dependencies import require_admin
from softarr.core.database import get_db
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.models.release import WorkflowState
from softarr.schemas.release import ReleaseResponse
from softarr.schemas.workflow import (
    BulkApproveRequest,
    BulkRejectRequest,
    OverrideRequest,
    WorkflowTransition,
)
from softarr.services.audit_service import AuditService
from softarr.services.release_service import ReleaseService

router = APIRouter()


@router.get("/queue")
async def get_staging_queue(
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Get releases in staging/review workflow states."""
    service = ReleaseService(db, ini)
    releases = await service.get_staging_queue()
    return {"staging": [ReleaseResponse.model_validate(r) for r in releases]}


@router.get("/discovered")
async def get_discovered(
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Get newly discovered releases not yet staged."""
    service = ReleaseService(db, ini)
    releases = await service.get_discovered_releases()
    return {"releases": [ReleaseResponse.model_validate(r) for r in releases]}


@router.get("/approved")
async def get_approved(
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Get approved releases ready for action."""
    service = ReleaseService(db, ini)
    releases = await service.get_approved_releases()
    return {"releases": [ReleaseResponse.model_validate(r) for r in releases]}


@router.post("/transition/{release_id}", response_model=ReleaseResponse)
async def transition_release(
    release_id: UUID,
    body: WorkflowTransition,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_admin),
):
    """Move a release to a new workflow state."""
    try:
        target = WorkflowState(body.target_state)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid workflow state: {body.target_state}",
        )

    service = ReleaseService(db, ini)
    try:
        release = await service.transition_state(
            release_id, target, changed_by=user.get("u", "admin")
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    audit = AuditService(db)
    await audit.log_action(
        "workflow_transition",
        "release",
        release_id,
        user=user.get("u", "admin"),
        details={
            "target_state": body.target_state,
            "reason": body.reason,
        },
    )
    return ReleaseResponse.model_validate(release)


@router.post("/approve/{release_id}", response_model=ReleaseResponse)
async def approve_release(
    release_id: UUID,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_admin),
):
    """Approve a release: updates workflow state and trust status atomically."""
    service = ReleaseService(db, ini)
    try:
        release = await service.approve_release(
            release_id, approved_by=user.get("u", "admin")
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    audit = AuditService(db)
    await audit.log_action(
        "approve",
        "release",
        release_id,
        user=user.get("u", "admin"),
        details={"name": release.name, "version": release.version},
    )
    return ReleaseResponse.model_validate(release)


@router.post("/override/{release_id}", response_model=ReleaseResponse)
async def override_release(
    release_id: UUID,
    override_in: OverrideRequest,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_admin),
):
    """Override a flagged release.

    Creates a ReleaseOverride record, applies admin trust, and transitions
    to approved state. Reason is required for restricted/blocked releases.
    Does not affect future releases from the same source.
    """
    service = ReleaseService(db, ini)
    try:
        release = await service.override_release(
            release_id,
            overridden_by=user.get("u", "admin"),
            reason=override_in.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    audit = AuditService(db)
    await audit.log_action(
        "override",
        "release",
        release_id,
        user=user.get("u", "admin"),
        details={
            "reason": override_in.reason,
            "name": release.name,
            "flag_status": release.flag_status.value if release.flag_status else "none",
        },
    )
    return ReleaseResponse.model_validate(release)


@router.post("/bulk-approve")
async def bulk_approve_releases(
    body: BulkApproveRequest,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_admin),
):
    """Approve multiple releases in one request.

    Returns a summary of which releases were approved and which failed
    (with the reason for failure).
    """
    service = ReleaseService(db, ini)
    result = await service.bulk_approve(body.release_ids, user=user.get("u", "admin"))
    audit = AuditService(db)
    await audit.log_action(
        "bulk_approve",
        "release",
        None,
        user=user.get("u", "admin"),
        details={
            "requested": len(body.release_ids),
            "approved": len(result.get("succeeded", [])),
            "failed": len(result.get("failed", [])),
        },
    )
    return result


@router.post("/bulk-reject")
async def bulk_reject_releases(
    body: BulkRejectRequest,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_admin),
):
    """Reject multiple releases in one request.

    Returns a summary of which releases were rejected and which failed
    (with the reason for failure).
    """
    service = ReleaseService(db, ini)
    result = await service.bulk_reject(
        body.release_ids,
        user=user.get("u", "admin"),
    )
    audit = AuditService(db)
    await audit.log_action(
        "bulk_reject",
        "release",
        None,
        user=user.get("u", "admin"),
        details={
            "requested": len(body.release_ids),
            "rejected": len(result.get("succeeded", [])),
            "failed": len(result.get("failed", [])),
            "reason": body.reason,
        },
    )
    return result
