"""MISP warninglists hash check.

Checks a SHA-256 hash against bundled MISP warninglists to identify known-good
or known-bad hashes without an external API call.

Warninglists are fetched on first use and cached in-memory. The bundle URL
points to the official MISP warninglists repository. Falls back gracefully if
the network is unavailable.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("softarr.hash_sources.misp")

MISP_WARNINGLISTS_URL = "https://raw.githubusercontent.com/MISP/misp-warninglists/main/lists/sha256-hashlookup/list.json"
REQUEST_TIMEOUT = 15

# Module-level cache to avoid refetching on every call
_known_good_hashes: Optional[set] = None
_cache_lock = asyncio.Lock()


async def _load_warninglists() -> set:
    """Fetch and cache the MISP warninglists known-good SHA-256 set."""
    global _known_good_hashes
    async with _cache_lock:
        if _known_good_hashes is not None:
            return _known_good_hashes
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.get(
                    MISP_WARNINGLISTS_URL, headers={"User-Agent": "softarr/1.0"}
                )
            if resp.status_code == 200:
                data = resp.json()
                # Format: {"list": ["hash1", "hash2", ...]}
                hashes = set(
                    h.lower() for h in data.get("list", []) if isinstance(h, str)
                )
                _known_good_hashes = hashes
                logger.info("Loaded %d MISP warninglists SHA-256 hashes", len(hashes))
                return hashes
        except Exception as exc:
            logger.warning("Failed to load MISP warninglists: %s", exc)
        _known_good_hashes = set()
        return set()


async def check_hash(sha256: str) -> Optional[Dict[str, Any]]:
    """Check a SHA-256 hash against MISP warninglists.

    Returns a dict with keys:
      - found (bool): whether the hash is in the known-good list
      - verdict (str): "known_good" if found, "unknown" otherwise

    Returns None on unexpected error.
    """
    try:
        known_good = await _load_warninglists()
        sha256_lower = sha256.lower()
        if sha256_lower in known_good:
            return {"found": True, "verdict": "known_good"}
        return {"found": False, "verdict": "unknown"}
    except Exception as exc:
        logger.warning("MISP warninglists check error: %s", exc)
        return None
