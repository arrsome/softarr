"""Vendor checksum fetcher.

Attempts to retrieve SHA-256 (or other) checksums co-located with a release
download URL by probing common checksum file naming conventions:

  - {url}.sha256
  - {url}.sha256sum
  - {base_dir}/SHA256SUMS
  - {base_dir}/checksums.txt
  - {base_dir}/CHECKSUMS

Returns a dict mapping algorithm names to hex digest strings, e.g.
``{"sha256": "abc123..."}`` or None if no checksum could be retrieved.
"""

import logging
import re
from typing import Dict, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

logger = logging.getLogger("softarr.hash_sources.vendor_checksums")

REQUEST_TIMEOUT = 10
MAX_CHECKSUM_BYTES = 64 * 1024  # 64 KB -- checksums files are small

# Patterns to extract hash from checksum file lines:
#   sha256sum format: <hash>  <filename>
#   bare hash lines
_HASH_LINE_RE = re.compile(
    r"^([0-9a-fA-F]{64})\s+.*$|^([0-9a-fA-F]{40})\s+.*$|^([0-9a-fA-F]{32})\s+.*$"
)
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
_SHA1_RE = re.compile(r"[0-9a-fA-F]{40}")
_MD5_RE = re.compile(r"[0-9a-fA-F]{32}")


def _candidate_urls(release_url: str, filename: str) -> list[str]:
    """Build a list of candidate checksum URLs from the release URL."""
    parts = urlsplit(release_url)
    # Base directory (strip filename)
    path_parts = parts.path.rsplit("/", 1)
    base_path = path_parts[0] + "/" if len(path_parts) > 1 else "/"
    base_url = urlunsplit((parts.scheme, parts.netloc, base_path, "", ""))

    candidates = [
        release_url + ".sha256",
        release_url + ".sha256sum",
        urljoin(base_url, "SHA256SUMS"),
        urljoin(base_url, "checksums.txt"),
        urljoin(base_url, "CHECKSUMS"),
    ]
    return candidates


def _parse_checksum_content(content: str, filename: str) -> Optional[Dict[str, str]]:
    """Parse checksum file content, returning the best matching hash for filename.

    Handles sha256sum-style ``<hash>  <filename>`` lines and bare hash lines.
    """
    filename_lower = filename.lower() if filename else ""
    best_sha256: Optional[str] = None

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Try sha256sum format: <hash>  <filename>
        parts = line.split(None, 1)
        if len(parts) == 2:
            candidate_hash, candidate_file = parts[0].lower(), parts[1].lower()
            # Strip leading * or ./ from filename in checksum lines
            candidate_file = candidate_file.lstrip("*./")
            if len(candidate_hash) == 64 and re.fullmatch(
                r"[0-9a-f]{64}", candidate_hash
            ):
                if (
                    not filename_lower
                    or filename_lower in candidate_file
                    or candidate_file in filename_lower
                ):
                    return {"sha256": candidate_hash}
                # Keep the first sha256 as fallback
                if best_sha256 is None:
                    best_sha256 = candidate_hash

    if best_sha256:
        return {"sha256": best_sha256}
    return None


async def fetch_vendor_checksums(
    release_url: str, filename: str = ""
) -> Optional[Dict[str, str]]:
    """Attempt to retrieve checksums for the given release URL.

    Probes candidate checksum URLs in order, returning the first match found.
    Returns ``{"sha256": "<hex>"}`` or None.
    """
    candidates = _candidate_urls(release_url, filename)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT, follow_redirects=True
    ) as client:
        for url in candidates:
            try:
                resp = await client.get(url, headers={"User-Agent": "softarr/1.0"})
                if resp.status_code != 200:
                    continue
                if len(resp.content) > MAX_CHECKSUM_BYTES:
                    logger.debug("Checksum file too large at %s, skipping", url)
                    continue
                result = _parse_checksum_content(resp.text, filename)
                if result:
                    logger.info(
                        "Retrieved checksum from %s for %s",
                        url,
                        filename or release_url,
                    )
                    return result
            except httpx.RequestError:
                continue

    return None
