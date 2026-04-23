"""VirusTotal hash lookup integration.

Queries the VirusTotal API v3 for a known SHA-256 hash. If the file has been
analysed before and any engine flagged it as malicious, the release is
marked BLOCKED.

Requires:
  - virustotal_enabled = true  in [hash_sources] of softarr.ini
  - virustotal_api_key set in [hash_sources]

The lookup is best-effort: network failures and unknown hashes are treated as
inconclusive (not an error) to avoid blocking releases unnecessarily.
"""

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("softarr.hash_sources.virustotal")

VT_API_URL = "https://www.virustotal.com/api/v3/files/{hash}"
VT_SUBMIT_URL = "https://www.virustotal.com/api/v3/urls"
REQUEST_TIMEOUT = 15


async def lookup(sha256: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Query VirusTotal for a SHA-256 hash.

    Returns a dict with keys:
      - malicious_count (int): number of engines that flagged the file
      - total_engines (int): total engines that scanned
      - permalink (str): VT report URL
      - found (bool): whether VT has a record for this hash

    Returns None on network error or if VT returns a non-200 status.
    """

    url = VT_API_URL.format(hash=sha256)
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(url, headers={"x-apikey": api_key})
    except httpx.RequestError as e:
        logger.warning("VirusTotal request failed: %s", e)
        return None

    if resp.status_code == 404:
        # Hash not known to VT -- inconclusive, not an error
        return {
            "found": False,
            "malicious_count": 0,
            "total_engines": 0,
            "permalink": "",
        }

    if resp.status_code != 200:
        logger.warning("VirusTotal returned HTTP %s", resp.status_code)
        return None

    try:
        data = resp.json()
        stats = (
            data.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
        )
        permalink = data.get("data", {}).get("links", {}).get("self", "")
        return {
            "found": True,
            "malicious_count": stats.get("malicious", 0),
            "total_engines": sum(stats.values()),
            "permalink": permalink,
        }
    except Exception as e:
        logger.warning("VirusTotal response parse error: %s", e)
        return None


async def submit_url_for_analysis(url: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Submit a URL to VirusTotal for analysis.

    Used when a hash lookup returns ``found: False`` to queue the download URL
    for scanning. Returns the analysis ID on success, or None on failure.
    This is a fire-and-forget operation -- the caller should not await for results.
    """
    if not url or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                VT_SUBMIT_URL,
                headers={"x-apikey": api_key},
                data={"url": url},
            )
        if resp.status_code in (200, 201):
            data = resp.json()
            analysis_id = data.get("data", {}).get("id", "") or data.get(
                "data", {}
            ).get("links", {}).get("self", "")
            logger.info("VirusTotal URL submitted for analysis, id=%s", analysis_id)
            return {"analysis_id": analysis_id, "url": url}
        logger.warning("VirusTotal URL submission returned HTTP %s", resp.status_code)
        return None
    except httpx.RequestError as e:
        logger.warning("VirusTotal URL submission failed: %s", e)
        return None
