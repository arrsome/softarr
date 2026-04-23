import logging
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.adapters.base import ReleaseSearchResult
from softarr.auth.dependencies import require_admin, require_auth
from softarr.core.config import settings
from softarr.core.database import get_db
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.middleware.rate_limit import limiter
from softarr.schemas.release import (
    PaginatedReleaseResponse,
    ReleaseCompareResponse,
    ReleaseDiff,
    ReleaseResponse,
)
from softarr.services.audit_service import AuditService
from softarr.services.release_service import ReleaseService
from softarr.utils.version import compare_versions

logger = logging.getLogger("softarr.releases")

router = APIRouter()


class BulkActionRequest(BaseModel):
    action: Literal["approve", "reject", "delete"]
    release_ids: List[UUID]


@router.get("/stats")
async def get_release_stats(
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Return aggregate release counts for dashboard stat cards."""
    service = ReleaseService(db, ini)
    return await service.get_release_stats()


@router.get("/search")
@limiter.limit(settings.RATE_LIMIT_SEARCH)
async def search_releases(
    request: Request,
    software_id: UUID,
    source_type: str = Query("github"),
    grouped: bool = Query(
        False, description="Group results by source/indexer, sorted by confidence score"
    ),
    search_mode: str = Query(
        "standard",
        description="Filter mode: standard, regex, fuzzy, exact, boolean",
    ),
    query: str = Query(
        "",
        description="Freeform query applied when search_mode is not 'standard'",
        max_length=256,
    ),
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
):
    """Search for releases using a source adapter.

    When grouped=true, results are grouped by source_type/indexer and sorted
    by confidence score (descending) within each group.

    search_mode selects an optional post-filter applied after adapter results
    are returned: standard (none), regex, fuzzy, exact, or boolean.
    """
    from softarr.services.search_filter_service import VALID_MODES, filter_results
    from softarr.services.software_service import SoftwareService

    # Validate search mode before hitting the adapter
    if search_mode not in VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid search_mode '{search_mode}'. Valid values: {', '.join(sorted(VALID_MODES))}",
        )

    service = ReleaseService(db, ini)
    try:
        results = await service.search_releases(software_id, source_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Fetch software name for richer audit context
    sw_service = SoftwareService(db)
    sw = await sw_service.get_software_by_id(software_id)
    canonical_name = sw.canonical_name if sw else str(software_id)

    # Count results with suspicious patterns for audit context
    flagged_count = sum(1 for r in results if r.raw_data.get("match_score", 1.0) < 0.5)

    audit = AuditService(db)
    await audit.log_action(
        "search",
        "release",
        software_id,
        details={
            "canonical_name": canonical_name,
            "source_type": source_type,
            "result_count": len(results),
            "flagged_count": flagged_count,
            "search_mode": search_mode,
        },
    )

    # Sort all results by match_score descending
    sorted_results = sorted(
        results,
        key=lambda r: r.raw_data.get("match_score", 0.0),
        reverse=True,
    )
    results_dicts = [r.model_dump() for r in sorted_results]

    # Apply optional post-filter
    try:
        results_dicts = filter_results(results_dicts, search_mode, query)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Anti-piracy scan -- when enabled, separate flagged results from clean ones.
    # Flagged results are returned in a separate list so the UI can present them
    # distinctly (red, collapsed) rather than silently dropping them.
    piracy_flagged: list[dict] = []
    antipiracy_enabled = (ini.get("antipiracy_enabled") or "false").lower() == "true"
    if antipiracy_enabled:
        from softarr.analysis.antipiracy import check_release_for_piracy

        clean: list[dict] = []
        for r in results_dicts:
            asset_names = list(r.get("unusual_files") or []) + list(
                r.get("suspicious_patterns") or []
            )
            hits = check_release_for_piracy(r.get("name", ""), asset_names)
            if hits:
                r["_piracy_hits"] = hits
                piracy_flagged.append(r)
            else:
                clean.append(r)
        results_dicts = clean

    response: Dict[str, Any] = {
        "results": results_dicts,
        "search_mode": search_mode,
        "piracy_flagged": piracy_flagged,
        "antipiracy_enabled": antipiracy_enabled,
    }

    if grouped:
        # Group by indexer name (if present in raw_data) or source_type
        grouped_map: Dict[str, list] = {}
        for r in results_dicts:
            group_key = (
                r.get("raw_data", {}).get("indexer")
                or r.get("source_type")
                or "unknown"
            )
            grouped_map.setdefault(group_key, []).append(r)
        response["grouped"] = grouped_map

    return response


@router.get("/nfo")
@limiter.limit("30/minute")
async def fetch_release_nfo(
    request: Request,
    indexer_name: str = Query(..., description="Configured indexer name"),
    nzb_guid: str = Query(..., description="NZB GUID or ID from the indexer"),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
):
    """Fetch NFO content for a release from a configured Newznab indexer.

    This is a lazy-load endpoint -- only called when the user explicitly
    requests NFO content for a result. The indexer name is validated against
    configured indexers to prevent SSRF.
    """
    import html

    import httpx

    from softarr.services.usenet_indexer_service import UsenetIndexerService

    indexer_svc = UsenetIndexerService(ini)
    indexers = {i.name: i for i in indexer_svc.get_all() if i.enabled}

    if indexer_name not in indexers:
        raise HTTPException(status_code=404, detail="Indexer not found or not enabled")

    indexer = indexers[indexer_name]
    base_url = indexer.url.rstrip("/")
    params = {
        "t": "details",
        "id": nzb_guid,
        "apikey": indexer.api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base_url}/api", params=params)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Indexer returned HTTP {resp.status_code}",
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Indexer request timed out")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Indexer unreachable: {exc}")

    # Parse NFO from Newznab details XML
    import xml.etree.ElementTree as ET

    nfo_text = ""
    try:
        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is not None:
            item = channel.find("item")
            if item is not None:
                # newznab:attr name="nfo" or description field
                for attr in item.findall(
                    "{http://www.newznab.com/DTD/2010/feeds/attributes/}attr"
                ):
                    if attr.get("name") == "nfo":
                        nfo_text = attr.get("value", "")
                        break
                if not nfo_text:
                    nfo_text = item.findtext("description", "")
    except ET.ParseError:
        nfo_text = ""

    # Sanitise -- return plain text only; strip HTML entities
    nfo_text = html.unescape(nfo_text).strip()

    return {"indexer": indexer_name, "guid": nzb_guid, "nfo": nfo_text}


@router.post("/process", response_model=ReleaseResponse)
@limiter.limit("20/minute")
async def process_release(
    request: Request,
    software_id: UUID,
    search_result: ReleaseSearchResult,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
):
    """Process a search result through the full analysis pipeline and store it."""
    service = ReleaseService(db, ini)
    try:
        release = await service.process_and_store_release(search_result, software_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Release processing failed for software %s: %s", software_id, e)
        raise HTTPException(status_code=500, detail="Release processing failed")

    audit = AuditService(db)
    await audit.log_action(
        "process",
        "release",
        release.id,
        details={
            "name": release.name,
            "version": release.version,
            "flag_status": release.flag_status.value if release.flag_status else "none",
        },
    )
    return ReleaseResponse.model_validate(release)


@router.get("/by-software/{software_id}", response_model=List[ReleaseResponse])
async def get_releases_by_software(
    software_id: UUID,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
):
    service = ReleaseService(db, ini)
    return await service.get_releases_by_software(software_id)


@router.get("/all", response_model=PaginatedReleaseResponse)
async def get_all_releases(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    software_id: Optional[UUID] = Query(None),
    trust_status: Optional[str] = Query(None),
    flag_status: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    workflow_state: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
):
    """Return a paginated, optionally filtered list of releases.

    Supports filtering by software_id, trust_status, flag_status, source_type,
    and workflow_state. Returns partial HTML rows when called from HTMX.
    """
    service = ReleaseService(db, ini)
    releases, total = await service.get_filtered_releases(
        page=page,
        page_size=page_size,
        software_id=software_id,
        trust_status=trust_status,
        flag_status=flag_status,
        source_type=source_type,
        workflow_state=workflow_state,
    )
    items = [ReleaseResponse.model_validate(r) for r in releases]
    return PaginatedReleaseResponse.build(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/bulk-action")
async def bulk_action(
    body: BulkActionRequest,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_admin),
):
    """Perform a bulk action (approve, reject, delete) on multiple releases."""
    service = ReleaseService(db, ini)
    username = user.get("u", "admin")

    if body.action == "approve":
        result = await service.bulk_approve(body.release_ids, user=username)
    elif body.action == "reject":
        result = await service.bulk_reject(body.release_ids, user=username)
    elif body.action == "delete":
        result = await service.bulk_delete(body.release_ids)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")

    audit = AuditService(db)
    await audit.log_action(
        f"bulk_{body.action}",
        "release",
        None,
        user=username,
        details={
            "action": body.action,
            "count": len(body.release_ids),
            "succeeded": len(result["succeeded"]),
            "failed": len(result["failed"]),
        },
    )
    return result


@router.delete("/{release_id}", status_code=204)
async def delete_release(
    release_id: UUID,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_admin),
):
    """Delete a release record and its associated analysis data."""
    service = ReleaseService(db, ini)
    deleted = await service.delete_release(release_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Release not found")
    audit = AuditService(db)
    await audit.log_action(
        "delete",
        "release",
        release_id,
        user=user.get("u", "admin"),
    )


# ---------------------------------------------------------------------------
# Release comparison helpers
# ---------------------------------------------------------------------------

_COMPARABLE_FIELDS = [
    "name",
    "version",
    "supported_os",
    "architecture",
    "publisher",
    "source_type",
    "source_origin",
    "confidence_score",
    "trust_status",
    "flag_status",
    "workflow_state",
    "flag_reasons",
    "unusual_files",
    "suspicious_patterns",
]


def _normalise(value: Any) -> Any:
    """Normalise enum instances to their string value for comparison."""
    return value.value if hasattr(value, "value") else value


def _build_recommendation(
    ra: ReleaseResponse,
    rb: ReleaseResponse,
    newer_version: str,
    diffs: List[ReleaseDiff],
) -> str:
    parts = []

    if ra.confidence_score > rb.confidence_score:
        parts.append("Release A has a higher confidence score")
    elif rb.confidence_score > ra.confidence_score:
        parts.append("Release B has a higher confidence score")

    fa = _normalise(ra.flag_status)
    fb = _normalise(rb.flag_status)
    if fa != fb:
        if fa == "none":
            parts.append("Release A has no flags")
        elif fb == "none":
            parts.append("Release B has no flags")

    if len(ra.flag_reasons) < len(rb.flag_reasons):
        parts.append("Release A has fewer flag reasons")
    elif len(rb.flag_reasons) < len(ra.flag_reasons):
        parts.append("Release B has fewer flag reasons")

    if newer_version == "a":
        parts.append("Release A is the newer version")
    elif newer_version == "b":
        parts.append("Release B is the newer version")

    if not parts:
        return "Releases are equivalent across all compared fields."
    return ". ".join(parts) + "."


def build_compare_response(
    ra: ReleaseResponse, rb: ReleaseResponse
) -> ReleaseCompareResponse:
    """Build a comparison between two releases, returning diffs and a recommendation."""
    diffs: List[ReleaseDiff] = []
    for field in _COMPARABLE_FIELDS:
        va = _normalise(getattr(ra, field, None))
        vb = _normalise(getattr(rb, field, None))
        if va != vb:
            diffs.append(ReleaseDiff(field=field, a_value=va, b_value=vb))

    cmp = compare_versions(ra.version, rb.version)
    if cmp > 0:
        newer_version: Literal["a", "b", "equal"] = "a"
    elif cmp < 0:
        newer_version = "b"
    else:
        newer_version = "equal"

    recommendation = _build_recommendation(ra, rb, newer_version, diffs)
    return ReleaseCompareResponse(
        release_a=ra,
        release_b=rb,
        differences=diffs,
        newer_version=newer_version,
        recommendation=recommendation,
    )


@router.get("/compare", response_model=ReleaseCompareResponse)
async def compare_releases(
    a: UUID = Query(..., description="UUID of release A"),
    b: UUID = Query(..., description="UUID of release B"),
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
):
    """Compare two releases side by side.

    Returns both releases in full, a list of field-level differences, the newer
    version identifier, and a plain-English recommendation based on confidence
    score, flag status, and version.
    """
    if a == b:
        raise HTTPException(status_code=400, detail="Release IDs must be different")

    service = ReleaseService(db, ini)
    rel_a = await service.get_release_by_id(a)
    rel_b = await service.get_release_by_id(b)

    if not rel_a:
        raise HTTPException(status_code=404, detail=f"Release A not found: {a}")
    if not rel_b:
        raise HTTPException(status_code=404, detail=f"Release B not found: {b}")

    ra = ReleaseResponse.model_validate(rel_a)
    rb = ReleaseResponse.model_validate(rel_b)
    return build_compare_response(ra, rb)


@router.get("/{release_id}", response_model=ReleaseResponse)
async def get_release(
    release_id: UUID,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
):
    service = ReleaseService(db, ini)
    release = await service.get_release_by_id(release_id)
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    return ReleaseResponse.model_validate(release)


@router.get("/{release_id}/hash-intelligence")
async def get_hash_intelligence(
    release_id: UUID,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user=Depends(require_auth),
):
    """Return all hash intelligence records for a release."""
    from softarr.services.hash_intelligence_service import HashIntelligenceService

    service = HashIntelligenceService(db, ini)
    records = await service.get_intelligence(release_id)
    return [
        {
            "id": str(r.id),
            "source": r.source,
            "verdict": r.verdict,
            "confidence": r.confidence,
            "sha256": r.sha256,
            "checked_at": r.checked_at.isoformat() if r.checked_at else None,
            "recheck_after": r.recheck_after.isoformat() if r.recheck_after else None,
            "raw_response": r.raw_response,
        }
        for r in records
    ]
