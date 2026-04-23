"""Usenet / NZB release adapter.

Searches Newznab-compatible indexers for software releases and normalises
results into the common ReleaseSearchResult pipeline. Disabled by default;
must be explicitly enabled via the usenet_adapter_enabled setting.

Supports:
  - Newznab API search (t=search, cat=4000 for apps/PC)
  - NZB result normalisation into ReleaseSearchResult
  - Multiple indexer configuration (future)
  - PAR2/segment deduplication -- collapses multi-part posts into one result
  - Alias-aware fuzzy filtering -- removes unrelated hits
  - Publisher inference from expected_publisher hint

Limitations in v0.1.x:
  - Single indexer only (first configured)
  - No NZB file download (URL handoff to SABnzbd instead)
  - Basic category mapping
"""

import difflib
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

from softarr.adapters.base import BaseAdapter, ReleaseSearchResult

logger = logging.getLogger("softarr.usenet")

# Newznab category IDs for software/applications
NEWZNAB_APP_CATEGORIES = "4000,4010,4020,4030,4040,4050,4060,4070"
MAX_RESPONSE_SIZE = 2 * 1024 * 1024  # 2 MB
REQUEST_TIMEOUT = 20

# Minimum fuzzy match ratio to include a result (0-1)
FUZZY_MATCH_THRESHOLD = 0.6

# Regex patterns for segment/recovery file detection in titles
_SEGMENT_PATTERNS = re.compile(
    r"""
    \bvol\d+\+\d+\.par2\b     # vol000+39.PAR2
    | \.par2\b                  # any .par2 extension
    | \.sfv\b                   # SFV checksum file
    | \.nfo\b                   # NFO info file
    | \bpart\d+\.rar\b          # partN.rar split archive
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Regex to extract a quoted filename from a title
_QUOTED_FILENAME_RE = re.compile(r'["\u201c\u201d]([^"]+)["\u201c\u201d]')

# Segment prefix patterns like "(01/21) - Description -" or "[04/14] -"
_SEGMENT_PREFIX_RE = re.compile(r"^[\(\[]\d+/\d+[\)\]]\s*-\s*(?:Description\s*-)?\s*")

# Testen/noise prefix like "Testen voor de gein (speed) 20 21- "
# Only matches when followed by at least one space (to avoid matching "x64-86")
_NOISE_PREFIX_RE = re.compile(r"^[^\"]*?\b\d+\s+\d+\s*-\s+", re.IGNORECASE)

# Extensions to strip from display names
_EXTENSION_STRIP_RE = re.compile(
    r"\.(par2|sfv|nfo|nzb|vol\d+\+\d+\.par2)$", re.IGNORECASE
)

# Architecture strings to infer supported_os from title
_OS_HINTS = {
    "win": "windows",
    "windows": "windows",
    "linux": "linux",
    "macos": "macos",
    "mac": "macos",
    "osx": "macos",
}


@dataclass
class UsenetIndexerConfig:
    """Configuration for a single Newznab-compatible indexer."""

    name: str
    url: str
    api_key: str
    enabled: bool = True
    categories: str = NEWZNAB_APP_CATEGORIES


class UsenetAdapter(BaseAdapter):
    name = "Usenet / NZB"
    source_type = "usenet"

    def __init__(self, indexers: Optional[List[UsenetIndexerConfig]] = None, ini=None):
        self.indexers = indexers or []
        self._ini = ini  # Optional IniSettingsManager for health stats recording

    async def search_releases(
        self, software: Dict, query: str | None = None
    ) -> List[ReleaseSearchResult]:
        """Search Newznab indexers for releases matching the software definition."""
        if not self.indexers:
            logger.debug("No Usenet indexers configured; returning empty results")
            return []

        results = []
        search_query = query or software.get("canonical_name", "")

        for indexer in self.indexers:
            if not indexer.enabled:
                continue
            import time

            start_ms = int(time.monotonic() * 1000)
            try:
                indexer_results = await self._search_indexer(
                    indexer, search_query, software
                )
                results.extend(indexer_results)
                elapsed_ms = int(time.monotonic() * 1000) - start_ms
                if self._ini:
                    try:
                        self._ini.record_indexer_result(indexer.name, True, elapsed_ms)
                    except Exception:
                        pass
            except Exception as e:
                elapsed_ms = int(time.monotonic() * 1000) - start_ms
                logger.warning(
                    "Usenet search failed for indexer '%s': %s",
                    indexer.name,
                    e,
                )
                if self._ini:
                    try:
                        self._ini.record_indexer_result(indexer.name, False, elapsed_ms)
                    except Exception:
                        pass

        return results

    async def fetch_release_details(self, release_url: str) -> Dict:
        """Fetch NZB details. For Usenet, this returns the NZB download URL
        which can be handed off to SABnzbd."""
        return {
            "url": release_url,
            "type": "nzb",
            "handoff": "sabnzbd",
        }

    async def _search_indexer(
        self,
        indexer: UsenetIndexerConfig,
        query: str,
        software: Dict,
    ) -> List[ReleaseSearchResult]:
        """Search a single Newznab-compatible indexer."""
        base_url = indexer.url.rstrip("/")
        params = {
            "t": "search",
            "q": query,
            "cat": indexer.categories,
            "apikey": indexer.api_key,
            "limit": 20,
            "extended": "1",
        }

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(f"{base_url}/api", params=params)

            if resp.status_code != 200:
                raise RuntimeError(f"Indexer returned HTTP {resp.status_code}")

            if len(resp.content) > MAX_RESPONSE_SIZE:
                raise RuntimeError("Indexer response exceeded size limit")

            return self._parse_newznab_response(resp.text, indexer, software)

    def _parse_newznab_response(
        self,
        xml_text: str,
        indexer: UsenetIndexerConfig,
        software: Dict,
    ) -> List[ReleaseSearchResult]:
        """Parse Newznab XML response into ReleaseSearchResult objects.

        Performs:
        - Segment/PAR2 deduplication: collapses multi-part posts into the
          largest single result per (display_name, version) group.
        - Alias-aware fuzzy filtering: drops results where neither the
          canonical name nor any alias matches the title.
        - Publisher inference from expected_publisher hint.
        """
        results = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning("Failed to parse Newznab XML: %s", e)
            return results

        channel = root.find("channel")
        if channel is None:
            return results

        canonical_name = software.get("canonical_name", "")
        aliases = software.get("aliases") or []
        expected_publisher = software.get("expected_publisher")

        # Build a deduplication map: (display_name, version) -> best result
        # "best" means largest size (the complete NZB, not a recovery block)
        dedup: Dict[tuple, Dict] = {}

        for item in channel.findall("item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")

            if not title or not link:
                continue

            # Skip recovery/segment files -- they are not usable standalone NZBs
            if _SEGMENT_PATTERNS.search(title):
                continue

            # Fuzzy-match against canonical name and aliases
            if not self._fuzzy_matches(canonical_name, title, aliases):
                continue

            display_name = self._clean_title(title)
            version = self._extract_version(title)
            publisher = self._infer_publisher(title, expected_publisher)

            # Build NZB download URL from enclosure or link
            enclosure = item.find("enclosure")
            if enclosure is not None:
                nzb_url = enclosure.get("url", link)
            else:
                nzb_url = link
            # Strip any trailing whitespace that may have been in the XML
            nzb_url = nzb_url.strip()

            # Extract newznab attributes
            raw_data: Dict = {"pub_date": pub_date, "indexer": indexer.name}
            for attr in item.findall(
                "{http://www.newznab.com/DTD/2010/feeds/attributes/}attr"
            ):
                raw_data[attr.get("name", "")] = attr.get("value", "")

            size = int(raw_data.get("size", 0) or 0)

            # Calculate fuzzy match score for sorting
            match_score = self._match_score(canonical_name, title, aliases)
            raw_data["match_score"] = round(match_score, 3)

            # Enrich raw_data with parsed metadata for the preview panel
            raw_data["release_group"] = self._extract_release_group(title)
            raw_data["file_count"] = int(raw_data.get("files", 0) or 0)
            raw_data["category"] = raw_data.get("category", "")
            raw_data["filename_parts"] = self._parse_filename_parts(display_name)
            raw_data["install_type"] = self._infer_install_type(title, display_name)
            raw_data["platform"] = raw_data["filename_parts"].get("platform", "")
            raw_data["arch"] = raw_data["filename_parts"].get("arch", "")

            candidate = {
                "display_name": display_name,
                "version": version,
                "publisher": publisher,
                "nzb_url": nzb_url,
                "size": size,
                "raw_data": raw_data,
                "title": title,
            }

            dedup_key = (display_name.lower(), version)
            existing = dedup.get(dedup_key)
            if existing is None or size > existing["size"]:
                dedup[dedup_key] = candidate

        # Convert deduplicated candidates to ReleaseSearchResult, sorted by
        # match_score descending then size descending
        sorted_candidates = sorted(
            dedup.values(),
            key=lambda c: (c["raw_data"].get("match_score", 0), c["size"]),
            reverse=True,
        )

        for c in sorted_candidates:
            results.append(
                ReleaseSearchResult(
                    name=c["title"],
                    display_name=c["display_name"],
                    version=c["version"],
                    supported_os=software.get("supported_os", []),
                    architecture=software.get("architecture"),
                    publisher=c["publisher"],
                    source_type=self.source_type,
                    source_origin=c["nzb_url"],
                    raw_data=c["raw_data"],
                )
            )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_title(title: str) -> str:
        """Extract a clean display name from a noisy Usenet release title.

        Strips segment prefixes, noise prefixes, and quoted filenames down
        to a readable software identifier. For example:
          'Testen voor de gein (speed) 20 21- "LibreOffice_26.2.1_Win_x86-64.nzb"'
          -> 'LibreOffice_26.2.1_Win_x86-64'
        """
        # 1. Try to extract a quoted filename first -- most informative
        quoted = _QUOTED_FILENAME_RE.search(title)
        if quoted:
            name = quoted.group(1).strip()
            # Strip known extensions from the extracted filename
            name = re.sub(
                r"\.(nzb|par2|sfv|nfo|rar|zip|iso|msi|exe|dmg)(\.\w+)?$",
                "",
                name,
                flags=re.IGNORECASE,
            )
            return name.strip()

        # 2. Strip common segment prefixes like "(01/21) - Description -"
        name = _SEGMENT_PREFIX_RE.sub("", title).strip()

        # 3. Strip noise prefixes like "Testen voor de gein (speed) 20 21-"
        # Only apply if the result looks like a software name (contains version-like pattern)
        if re.search(r"\d+\.\d+", name) and _NOISE_PREFIX_RE.match(name):
            cleaned = _NOISE_PREFIX_RE.sub("", name).strip()
            if cleaned:
                name = cleaned

        # 4. Strip trailing size annotations like "- 385,85 MB"
        name = re.sub(r"\s*-\s*[\d,\.]+\s*[MG]B\s*$", "", name).strip()

        return name or title

    @staticmethod
    def _fuzzy_matches(canonical: str, title: str, aliases: List[str]) -> bool:
        """Return True if the canonical name or any alias fuzzy-matches the title.

        Uses SequenceMatcher ratio against title words/substrings.
        Also checks for simple case-insensitive substring containment.
        """
        title_lower = title.lower()
        candidates = [canonical] + (aliases or [])

        for term in candidates:
            if not term:
                continue
            term_lower = term.lower()
            # Fast path: exact substring match
            if term_lower in title_lower:
                return True
            # Fuzzy path: compare against title
            ratio = difflib.SequenceMatcher(None, term_lower, title_lower).ratio()
            if ratio >= FUZZY_MATCH_THRESHOLD:
                return True

        return False

    @staticmethod
    def _match_score(canonical: str, title: str, aliases: List[str]) -> float:
        """Return the best fuzzy match ratio for ranking results."""
        title_lower = title.lower()
        candidates = [canonical] + (aliases or [])
        best = 0.0
        for term in candidates:
            if not term:
                continue
            term_lower = term.lower()
            if term_lower in title_lower:
                return 1.0
            ratio = difflib.SequenceMatcher(None, term_lower, title_lower).ratio()
            if ratio > best:
                best = ratio
        return best

    @staticmethod
    def _infer_publisher(
        title: str, expected_publisher: Optional[str]
    ) -> Optional[str]:
        """Infer publisher from title when expected_publisher is configured.

        If the expected_publisher string appears in the title (case-insensitive),
        return it as the publisher. This covers common patterns like
        "The Document Foundation LibreOffice..." or just presence of the brand name.
        """
        if not expected_publisher:
            return None
        if expected_publisher.lower() in title.lower():
            return expected_publisher
        return None

    @staticmethod
    def _extract_version(title: str) -> str:
        """Try to extract a version number from a release title.

        Handles patterns like:
          - LibreOffice_26.2.1_Win_x86-64  -> 26.2.1
          - AppName v1.2.3                  -> 1.2.3
          - AppName 2025 v25.8.1            -> 25.8.1 (prefers longer match)
        """
        # Find all version-like patterns and return the most specific (most components)
        matches = re.findall(r"v?(\d+\.\d+(?:\.\d+)?(?:\.\d+)?)", title)
        if not matches:
            return "unknown"
        # Prefer the match with the most version components
        return max(matches, key=lambda v: len(v.split(".")))

    @staticmethod
    def _extract_release_group(title: str) -> str:
        """Extract the release group from a Usenet title.

        Common patterns:
          - AppName.v1.0.0-GROUPNAME
          - AppName 1.0 [GROUP]
        """
        # Trailing dash-group pattern: "-GROUPNAME" at end of title (all caps or mixed)
        m = re.search(r"-([A-Z][A-Z0-9]{1,15})(?:\s*$|\s*\[)", title)
        if m:
            return m.group(1)
        # Square bracket group pattern: "[GROUP]"
        m = re.search(r"\[([A-Z][A-Z0-9]{1,15})\]", title)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _parse_filename_parts(display_name: str) -> Dict[str, str]:
        """Extract structured fields from a display name.

        Returns a dict with keys: platform, arch, language, edition.
        All values are strings; empty string if not detected.
        """
        name = display_name.lower()
        parts: Dict[str, str] = {
            "platform": "",
            "arch": "",
            "language": "",
            "edition": "",
        }

        # Platform detection
        if any(k in name for k in ("win", "windows", "x86-64", "x64", "x86")):
            parts["platform"] = "Windows"
        elif any(k in name for k in ("linux", "linux64", "deb", "rpm")):
            parts["platform"] = "Linux"
        elif any(k in name for k in ("mac", "macos", "osx", "dmg")):
            parts["platform"] = "macOS"

        # Architecture detection
        if any(k in name for k in ("x86-64", "x86_64", "x64", "amd64")):
            parts["arch"] = "x64"
        elif "arm64" in name or "aarch64" in name:
            parts["arch"] = "ARM64"
        elif "arm" in name:
            parts["arch"] = "ARM"
        elif any(k in name for k in ("x86", "i686", "i386", "32bit")):
            parts["arch"] = "x86"

        # Language detection (ISO 639-1 / common labels)
        lang_match = re.search(
            r"[\._-](en|de|fr|es|it|nl|pl|pt|ru|zh|ja|ko|ar|tr|sv|da|fi|nb|uk)"
            r"(?:[\._-]|$)",
            name,
        )
        if lang_match:
            parts["language"] = lang_match.group(1).upper()
        elif "multilingual" in name or "multi" in name:
            parts["language"] = "Multi"

        # Edition detection
        if "portable" in name or "standalone" in name:
            parts["edition"] = "Portable"
        elif "enterprise" in name:
            parts["edition"] = "Enterprise"
        elif "professional" in name or "pro" in name:
            parts["edition"] = "Professional"

        return parts

    @staticmethod
    def _infer_install_type(title: str, display_name: str) -> str:
        """Heuristic: infer installer, portable, or archive from name cues."""
        combined = (title + " " + display_name).lower()
        if any(k in combined for k in ("portable", "standalone", "noinstall")):
            return "portable"
        if any(
            k in combined for k in ("setup", "install", ".exe", ".msi", ".dmg", ".pkg")
        ):
            return "installer"
        if any(
            k in combined for k in (".zip", ".tar", ".gz", ".7z", ".rar", "archive")
        ):
            return "archive"
        return "unknown"
