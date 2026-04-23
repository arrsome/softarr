"""CIRCL hashlookup integration.

Queries the CIRCL hashlookup API for a known SHA-256 hash. No API key required.
Known-good hashes (present in the NSRL / trusted software database) return a
result dict; unknown hashes return ``{"found": False}``.

See https://hashlookup.circl.lu/ for API documentation.
"""

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("softarr.hash_sources.circl")

CIRCL_API_URL = "https://hashlookup.circl.lu/lookup/sha256/{hash}"
REQUEST_TIMEOUT = 10


async def lookup(sha256: str) -> Optional[Dict[str, Any]]:
    """Query CIRCL hashlookup for a SHA-256 hash.

    Returns a dict with keys:
      - found (bool): whether the hash is in the database
      - product_name (str): product name if found
      - publisher (str): publisher / vendor if found
      - raw (dict): full API response

    Returns None on network error.
    """
    url = CIRCL_API_URL.format(hash=sha256.lower())
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": "softarr/1.0"})
    except httpx.RequestError as exc:
        logger.warning("CIRCL hashlookup request failed: %s", exc)
        return None

    if resp.status_code == 404:
        return {"found": False, "product_name": "", "publisher": ""}

    if resp.status_code != 200:
        logger.warning("CIRCL hashlookup returned HTTP %d", resp.status_code)
        return None

    try:
        data = resp.json()
        return {
            "found": True,
            "product_name": data.get("ProductName") or data.get("FileName") or "",
            "publisher": data.get("CompanyName") or data.get("SpecialCode") or "",
            "raw": data,
        }
    except Exception as exc:
        logger.warning("CIRCL hashlookup response parse error: %s", exc)
        return None
