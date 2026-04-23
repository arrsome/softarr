import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.dependencies import require_admin, require_auth, require_viewer
from softarr.core.database import AsyncSessionLocal, get_db
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.models.software import Software
from softarr.schemas.software import (
    PaginatedSoftwareResponse,
    SoftwareCreate,
    SoftwareResponse,
    SoftwareUpdate,
)
from softarr.services.audit_service import AuditService
from softarr.services.release_service import ReleaseService
from softarr.services.software_service import SoftwareService
from softarr.utils.version import compare_versions
from softarr.version import __version__ as _APP_VERSION

router = APIRouter()


@router.post(
    "/",
    response_model=SoftwareResponse,
    status_code=201,
    summary="Create software entry",
)
async def create_software(
    software_in: SoftwareCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new software entry in the allowlist library."""
    service = SoftwareService(db)
    result = await service.create_software(software_in)
    audit = AuditService(db)
    await audit.log_action(
        "create",
        "software",
        result.id,
        user=user.get("u", "admin"),
        details={
            "canonical_name": result.canonical_name,
        },
    )
    return result


@router.get(
    "/",
    response_model=PaginatedSoftwareResponse,
    summary="List all software",
)
async def list_software(
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(50, ge=1, le=200, description="Entries per page"),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    """Return a paginated list of software entries in the allowlist library."""
    service = SoftwareService(db)
    items, total = await service.get_all_software_paginated(
        page=page, page_size=page_size
    )
    return PaginatedSoftwareResponse.build(
        items=items, total=total, page=page, page_size=page_size
    )


@router.get(
    "/catalogue", summary="Open-source software catalogue with availability flags"
)
async def get_opensource_catalogue(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    """Return the built-in open-source software catalogue, annotated with already_added flags.

    Each entry in the catalogue includes an ``already_added`` boolean indicating
    whether a software entry with the same canonical name already exists in the library.
    """
    from softarr.data.opensource_catalogue import CATALOGUE

    result = await db.execute(select(Software.canonical_name))
    existing_names = {row[0].lower() for row in result.all()}
    annotated = [
        {**entry, "already_added": entry["canonical_name"].lower() in existing_names}
        for entry in CATALOGUE
    ]
    return {"catalogue": annotated}


@router.get("/wanted/search-all")
async def search_all_wanted(
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Trigger a release search for all wanted (no-download) software entries.

    Streams Server-Sent Events with per-software progress updates.
    The final event is ``{"done": true}``.
    """

    async def _stream() -> AsyncGenerator[bytes, None]:
        from softarr.models.release import Release, WorkflowState

        async with AsyncSessionLocal() as db:
            # Get all wanted software
            result = await db.execute(
                select(Software)
                .where(Software.monitored == True)  # noqa: E712
                .where(Software.is_active == True)  # noqa: E712
                .order_by(Software.canonical_name)
            )
            all_monitored = result.scalars().all()

            wanted = []
            for sw in all_monitored:
                dl_result = await db.execute(
                    select(Release)
                    .where(
                        Release.software_id == sw.id,
                        Release.workflow_state == WorkflowState.DOWNLOADED,
                    )
                    .limit(1)
                )
                if dl_result.scalar_one_or_none() is None:
                    wanted.append(sw)

            release_service = ReleaseService(db, ini)
            for sw in wanted:
                try:
                    results = await release_service.search_releases(sw.id, "auto")
                    new_count = 0
                    for r in results:
                        existing = await release_service.get_by_version(
                            sw.id, r.version
                        )
                        if not existing:
                            await release_service.process_and_store_release(r, sw.id)
                            new_count += 1
                    event = {
                        "software": sw.canonical_name,
                        "found": len(results),
                        "new": new_count,
                        "status": "ok",
                    }
                except Exception as exc:
                    event = {
                        "software": sw.canonical_name,
                        "found": 0,
                        "new": 0,
                        "status": "error",
                        "error": str(exc),
                    }
                yield f"data: {json.dumps(event)}\n\n".encode()
                await asyncio.sleep(0.5)  # Rate limiting between searches

        yield b'data: {"done": true}\n\n'

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/wanted", response_model=List[SoftwareResponse])
async def get_wanted_software(
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
):
    """Return monitored software entries that have no downloaded release.

    This is the 'Wanted' view -- entries the user intends to track but
    has not yet successfully downloaded.
    """

    from softarr.models.release import Release, WorkflowState

    result = await db.execute(
        select(Software)
        .where(Software.monitored == True)  # noqa: E712
        .where(Software.is_active == True)  # noqa: E712
        .order_by(Software.canonical_name)
    )
    all_monitored = result.scalars().all()

    wanted = []
    for sw in all_monitored:
        # Check if a DOWNLOADED release exists for this software
        dl_result = await db.execute(
            select(Release)
            .where(
                Release.software_id == sw.id,
                Release.workflow_state == WorkflowState.DOWNLOADED,
            )
            .limit(1)
        )
        has_download = dl_result.scalar_one_or_none() is not None
        if not has_download:
            wanted.append(SoftwareResponse.model_validate(sw))

    return wanted


@router.get("/export")
async def export_software_library(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Export all software entries as a JSON library file."""
    service = SoftwareService(db)
    all_sw = await service.get_all_software()
    return {
        "softarr_version": _APP_VERSION,
        "exported_by": user.get("u", "admin"),
        "software": [s.model_dump() for s in all_sw],
    }


@router.post("/import", status_code=201)
async def import_software_library(
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Import software entries from a JSON library export.

    Skips entries whose canonical_name already exists.
    Returns a summary of created and skipped counts.
    """
    from softarr.schemas.software import SoftwareCreate

    entries = body.get("software", [])
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="'software' must be a list")

    service = SoftwareService(db)
    audit = AuditService(db)
    created, skipped = 0, 0

    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("canonical_name"):
            skipped += 1
            continue
        # Skip if already exists
        from sqlalchemy import select as sa_select

        from softarr.models.software import Software as SoftwareModel

        existing = await db.execute(
            sa_select(SoftwareModel).where(
                SoftwareModel.canonical_name == entry["canonical_name"]
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        try:
            sw_in = SoftwareCreate(
                canonical_name=entry["canonical_name"],
                aliases=entry.get("aliases") or [],
                expected_publisher=entry.get("expected_publisher"),
                supported_os=entry.get("supported_os") or [],
                architecture=entry.get("architecture"),
                version_format_rules=entry.get("version_format_rules") or {},
                source_preferences=entry.get("source_preferences") or [],
                notes=entry.get("notes"),
                monitored=entry.get("monitored", True),
                tags=entry.get("tags") or [],
                download_profile=entry.get("download_profile") or {},
            )
            result = await service.create_software(sw_in)
            await audit.log_action(
                "import",
                "software",
                result.id,
                user=user.get("u", "admin"),
                details={"canonical_name": result.canonical_name},
            )
            created += 1
        except Exception:
            skipped += 1

    return {"created": created, "skipped": skipped}


@router.get(
    "/{software_id}", response_model=SoftwareResponse, summary="Get software entry"
)
async def get_software(
    software_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    """Return a single software entry by ID."""
    service = SoftwareService(db)
    software = await service.get_software_by_id(software_id)
    if not software:
        raise HTTPException(status_code=404, detail="Software not found")
    return SoftwareResponse.model_validate(software)


@router.patch(
    "/{software_id}", response_model=SoftwareResponse, summary="Update software entry"
)
async def update_software(
    software_id: UUID,
    update_in: SoftwareUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Partially update an existing software entry. Only provided fields are changed."""
    service = SoftwareService(db)
    result = await service.update_software(software_id, update_in)
    if not result:
        raise HTTPException(status_code=404, detail="Software not found")
    audit = AuditService(db)
    await audit.log_action(
        "update", "software", software_id, user=user.get("u", "admin")
    )
    return result


@router.delete("/{software_id}", status_code=204, summary="Delete software entry")
async def delete_software(
    software_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Permanently delete a software entry and all associated releases."""
    service = SoftwareService(db)
    deleted = await service.delete_software(software_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Software not found")
    audit = AuditService(db)
    await audit.log_action(
        "delete", "software", software_id, user=user.get("u", "admin")
    )


@router.get("/{software_id}/check-version")
async def check_version(
    software_id: UUID,
    source_type: str = Query("usenet"),
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Check whether a newer version is available for a software entry.

    Searches the specified source for the latest version and compares it
    against the most recently downloaded release. Returns update status
    and the best candidate result if a newer version is found.
    """
    software_service = SoftwareService(db)
    software = await software_service.get_software_by_id(software_id)
    if not software:
        raise HTTPException(status_code=404, detail="Software not found")

    release_service = ReleaseService(db, ini)

    # Get the version we already have downloaded
    current_version: Optional[
        str
    ] = await release_service.get_latest_downloaded_version(software_id)

    # Search the source for current releases
    try:
        results = await release_service.search_releases(software_id, source_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not results:
        return {
            "current_version": current_version,
            "latest_found": None,
            "update_available": False,
            "latest_result": None,
        }

    # Find the result with the highest version
    best = max(
        results,
        key=lambda r: _version_tuple(r.version),
        default=None,
    )

    latest_version = best.version if best else None
    update_available = False
    if latest_version and latest_version != "unknown":
        if current_version is None:
            update_available = True
        else:
            update_available = compare_versions(latest_version, current_version) > 0

    return {
        "current_version": current_version,
        "latest_found": latest_version,
        "update_available": update_available,
        "latest_result": best.model_dump() if best else None,
    }


@router.get("/{software_id}/releases")
async def get_software_releases(
    software_id: UUID,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """Return all releases ever processed for a software entry, newest first."""
    from softarr.models.release import Release

    sw_service = SoftwareService(db)
    software = await sw_service.get_software_by_id(software_id)
    if not software:
        raise HTTPException(status_code=404, detail="Software not found")

    result = await db.execute(
        select(Release)
        .where(Release.software_id == software_id)
        .order_by(Release.created_at.desc())
        .limit(limit)
    )
    releases = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "version": r.version,
            "workflow_state": r.workflow_state.value,
            "source_type": r.source_type,
            "confidence_score": r.confidence_score,
            "trust_status": r.trust_status.value,
            "flag_status": r.flag_status.value,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in releases
    ]


@router.get("/{software_id}/suggested-threshold")
async def get_suggested_threshold(
    software_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """Return a suggested auto-approve threshold based on download history.

    Requires at least 5 clean downloads. Returns null if insufficient data.
    """
    sw_service = SoftwareService(db)
    software = await sw_service.get_software_by_id(software_id)
    if not software:
        raise HTTPException(status_code=404, detail="Software not found")

    from softarr.services.trust_service import TrustService

    trust_svc = TrustService(db)
    suggestion = await trust_svc.suggest_threshold(software_id)
    return {"software_id": str(software_id), "suggested_threshold": suggestion}


def _version_tuple(version: str) -> tuple:
    """Convert a version string to a comparable tuple of ints.

    Returns (0,) for unknown/unparseable versions so they sort lowest.
    """
    try:
        return tuple(int(x) for x in version.split(".") if x.isdigit())
    except ValueError, AttributeError:
        return (0,)
