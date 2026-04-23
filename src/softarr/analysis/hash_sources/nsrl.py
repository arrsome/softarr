"""NIST NSRL (National Software Reference Library) hash lookup.

Queries the public NSRL REST API to check whether a SHA-256 hash is in the
reference library of known-good software. A match boosts the release's
match_quality_score and can help elevate trust status.

No API key required -- the NSRL REST service is publicly accessible.

Endpoint: https://hash.nsrl.nist.gov/api/sha256/{hash}

The lookup is best-effort: network failures are treated as inconclusive.
"""

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("softarr.hash_sources.nsrl")

NSRL_API_URL = "https://hash.nsrl.nist.gov/api/sha256/{hash}"
REQUEST_TIMEOUT = 15


async def lookup(sha256: str) -> Optional[Dict[str, Any]]:
    """Query the NIST NSRL for a SHA-256 hash.

    Returns a dict with keys:
      - found (bool): whether the hash is in the NSRL
      - product_name (str | None): product name from NSRL if found
      - manufacturer (str | None): manufacturer name if available

    Returns None on network error.
    """
    url = NSRL_API_URL.format(hash=sha256.lower())
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url)
    except httpx.RequestError as e:
        logger.warning("NSRL request failed: %s", e)
        return None

    if resp.status_code == 404:
        return {"found": False, "product_name": None, "manufacturer": None}

    if resp.status_code != 200:
        logger.warning("NSRL returned HTTP %s", resp.status_code)
        return None

    try:
        data = resp.json()
        # NSRL REST API returns a list of matching records
        records = data if isinstance(data, list) else data.get("results", [])
        if not records:
            return {"found": False, "product_name": None, "manufacturer": None}
        first = records[0]
        return {
            "found": True,
            "product_name": first.get("ProductName") or first.get("product_name"),
            "manufacturer": first.get("MfgCode") or first.get("manufacturer"),
        }
    except Exception as e:
        logger.warning("NSRL response parse error: %s", e)
        return None
