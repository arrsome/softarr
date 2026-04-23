import logging
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.dependencies import require_admin
from softarr.core.database import get_db
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.schemas.usenet_indexer import (
    UsenetIndexerCreate,
    UsenetIndexerResponse,
    UsenetIndexerUpdate,
)
from softarr.services.audit_service import AuditService
from softarr.services.usenet_indexer_service import UsenetIndexerService

logger = logging.getLogger("softarr.indexers")

router = APIRouter()


@router.post("/", response_model=UsenetIndexerResponse, status_code=201)
async def create_indexer(
    body: UsenetIndexerCreate,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Create a new Usenet indexer configuration."""
    service = UsenetIndexerService(ini)
    try:
        result = service.create(body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    audit = AuditService(db)
    await audit.log_action(
        "create",
        "usenet_indexer",
        None,
        user=user.get("u", "admin"),
        details={"name": result.name, "url": result.url},
    )
    return result


@router.get("/", response_model=List[UsenetIndexerResponse])
async def list_indexers(
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """List all Usenet indexers ordered by priority."""
    service = UsenetIndexerService(ini)
    return service.get_all()


@router.get("/{indexer_name:path}", response_model=UsenetIndexerResponse)
async def get_indexer(
    indexer_name: str,
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Get a single Usenet indexer by name."""
    service = UsenetIndexerService(ini)
    result = service.get_by_name(indexer_name)
    if not result:
        raise HTTPException(status_code=404, detail="Indexer not found")
    return result


@router.patch("/{indexer_name:path}", response_model=UsenetIndexerResponse)
async def update_indexer(
    indexer_name: str,
    body: UsenetIndexerUpdate,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Partially update an existing Usenet indexer."""
    service = UsenetIndexerService(ini)
    try:
        result = service.update(indexer_name, body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Indexer not found")

    audit = AuditService(db)
    # Log updated fields but never the raw API key
    logged = {
        k: v for k, v in body.model_dump(exclude_unset=True).items() if k != "api_key"
    }
    if "api_key" in body.model_dump(exclude_unset=True):
        logged["api_key"] = "****"
    await audit.log_action(
        "update",
        "usenet_indexer",
        None,
        user=user.get("u", "admin"),
        details={"indexer_name": indexer_name, **logged},
    )
    return result


@router.get("/{indexer_name:path}/caps")
async def get_indexer_caps(
    indexer_name: str,
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Query the Newznab caps endpoint for an indexer and return its reported categories.

    Calls ``?t=caps&apikey=<key>`` on the indexer URL and parses the XML response.
    Returns a list of category objects with ``id`` and ``name`` fields.

    Useful for auto-populating the ``categories`` field when configuring a new indexer.
    """
    service = UsenetIndexerService(ini)
    indexer = service.get_by_name(indexer_name)
    if not indexer:
        raise HTTPException(status_code=404, detail="Indexer not found")

    caps_url = indexer.url.rstrip("/") + "/api"
    params = {"t": "caps", "apikey": indexer.api_key}

    try:
        async with httpx.AsyncClient(timeout=15, verify=indexer.ssl_verify) as client:
            resp = await client.get(caps_url, params=params)
        resp.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Indexer caps request timed out")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Indexer connection error: {exc}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Indexer returned HTTP {exc.response.status_code}",
        )

    # Parse XML categories
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid caps XML: {exc}")

    # Newznab caps XML has <categories><category id="..." name="..."> elements
    ns = {"newznab": "http://www.newznab.com/DTD/2010/feeds/attributes/"}
    categories = []

    # Try both with and without namespace
    for cat_el in root.iter("category"):
        cat_id = cat_el.get("id", "")
        cat_name = cat_el.get("name", "")
        if cat_id and cat_name:
            categories.append({"id": cat_id, "name": cat_name})
            # Include subcategories
            for sub_el in cat_el:
                sub_id = sub_el.get("id", "")
                sub_name = sub_el.get("name", "")
                if sub_id and sub_name:
                    categories.append(
                        {"id": sub_id, "name": f"{cat_name} / {sub_name}"}
                    )

    logger.info(
        "Caps query for indexer %r returned %d categories",
        indexer_name,
        len(categories),
    )
    return {"indexer": indexer_name, "categories": categories}


@router.get("/{indexer_name:path}/stats")
async def get_indexer_stats(
    indexer_name: str,
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Return health stats for a Usenet indexer.

    Stats are accumulated from live search calls and stored in the INI file.
    Returns success/failure counts, last response time, and last seen timestamps.
    """
    service = UsenetIndexerService(ini)
    if not service.get_by_name(indexer_name):
        raise HTTPException(status_code=404, detail="Indexer not found")

    stats = ini.get_indexer_stats(indexer_name)
    return {"indexer": indexer_name, **stats}


@router.delete("/{indexer_name:path}", status_code=204)
async def delete_indexer(
    indexer_name: str,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Delete a Usenet indexer."""
    service = UsenetIndexerService(ini)
    if not service.delete(indexer_name):
        raise HTTPException(status_code=404, detail="Indexer not found")

    audit = AuditService(db)
    await audit.log_action(
        "delete",
        "usenet_indexer",
        None,
        user=user.get("u", "admin"),
        details={"name": indexer_name},
    )
