"""Torznab / Torrent release adapter.

Searches Torznab-compatible indexers for software releases and normalises
results into the common ReleaseSearchResult pipeline. Torznab is the torrent
equivalent of Newznab -- it uses the same XML feed schema but the <link>
element (or enclosure URL) contains a .torrent file URL or magnet link
instead of an NZB URL.

Disabled by default; enable via the torznab_adapter_enabled setting and
configure at least one indexer with type=torznab.

Supports:
  - Torznab API search (t=search, cat=4000 for apps/PC)
  - Magnet link and .torrent URL normalisation into ReleaseSearchResult
  - Multiple indexer configuration (same INI indexer sections as Newznab,
    distinguished by type=torznab)
  - PAR2/segment deduplication (inherited from Newznab XML parsing)
  - Alias-aware fuzzy filtering

Pairs with qBittorrent for download queuing.
"""

import logging
from typing import Dict, List, Optional

import httpx

from softarr.adapters.base import BaseAdapter, ReleaseSearchResult
from softarr.adapters.usenet import (
    _SEGMENT_PATTERNS,
    MAX_RESPONSE_SIZE,
    REQUEST_TIMEOUT,
    UsenetAdapter,
    UsenetIndexerConfig,
)

logger = logging.getLogger("softarr.torznab")

# Torznab app/software category IDs (same Newznab taxonomy)
TORZNAB_APP_CATEGORIES = "4000,4010,4020,4030,4040,4050,4060,4070"


class TorznabAdapter(BaseAdapter):
    """Adapter for Torznab-compatible torrent indexers.

    Mirrors UsenetAdapter but returns torrent URLs/magnet links rather than
    NZB URLs. Reuses the XML parsing, fuzzy matching, and deduplication logic
    from UsenetAdapter.
    """

    name = "Torznab / Torrent"
    source_type = "torznab"

    def __init__(
        self,
        indexers: Optional[List[UsenetIndexerConfig]] = None,
        ini=None,
    ):
        self.indexers = indexers or []
        self._ini = ini  # Optional IniSettingsManager for health stats recording
        # Delegate XML parsing and helper methods to a UsenetAdapter instance.
        # The source_type on the results is overridden to "torznab" below.
        self._usenet = UsenetAdapter(indexers=[], ini=None)

    async def search_releases(
        self, software: Dict, query: str | None = None
    ) -> List[ReleaseSearchResult]:
        """Search Torznab indexers for releases matching the software definition."""
        if not self.indexers:
            logger.debug("No Torznab indexers configured; returning empty results")
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
            except Exception as exc:
                elapsed_ms = int(time.monotonic() * 1000) - start_ms
                logger.warning(
                    "Torznab search failed for indexer '%s': %s",
                    indexer.name,
                    exc,
                )
                if self._ini:
                    try:
                        self._ini.record_indexer_result(indexer.name, False, elapsed_ms)
                    except Exception:
                        pass

        return results

    async def fetch_release_details(self, release_url: str) -> Dict:
        """Return download metadata for the given torrent URL or magnet link.

        The URL is passed as-is to qBittorrent -- no server-side download needed.
        """
        return {
            "url": release_url,
            "type": "torrent",
            "handoff": "qbittorrent",
        }

    async def _search_indexer(
        self,
        indexer: UsenetIndexerConfig,
        query: str,
        software: Dict,
    ) -> List[ReleaseSearchResult]:
        """Search a single Torznab-compatible indexer."""
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

            return self._parse_torznab_response(resp.text, indexer, software)

    def _parse_torznab_response(
        self,
        xml_text: str,
        indexer: UsenetIndexerConfig,
        software: Dict,
    ) -> List[ReleaseSearchResult]:
        """Parse Torznab XML response into ReleaseSearchResult objects.

        Torznab uses the same XML schema as Newznab. The key difference is that
        the <link> element (or enclosure URL) contains a .torrent download URL
        or a magnet link.

        Reuses fuzzy matching and deduplication logic from UsenetAdapter.
        """
        import xml.etree.ElementTree as ET
        from typing import Dict as DictType

        results = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.warning("Failed to parse Torznab XML: %s", exc)
            return results

        channel = root.find("channel")
        if channel is None:
            return results

        canonical_name = software.get("canonical_name", "")
        aliases = software.get("aliases") or []
        expected_publisher = software.get("expected_publisher")

        dedup: DictType[tuple, dict] = {}

        for item in channel.findall("item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub_date = item.findtext("pubDate", "")

            if not title or not link:
                continue

            # Skip segment/recovery files
            if _SEGMENT_PATTERNS.search(title):
                continue

            # Fuzzy-match against canonical name and aliases
            if not self._usenet._fuzzy_matches(canonical_name, title, aliases):
                continue

            display_name = self._usenet._clean_title(title)
            version = self._usenet._extract_version(title)
            publisher = self._usenet._infer_publisher(title, expected_publisher)

            # Prefer enclosure URL for .torrent; fall back to link (may be magnet)
            enclosure = item.find("enclosure")
            torrent_url = enclosure.get("url", link) if enclosure is not None else link
            torrent_url = torrent_url.strip()

            # Extract torznab/newznab attributes
            raw_data: dict = {"pub_date": pub_date, "indexer": indexer.name}
            for attr in item.findall(
                "{http://www.newznab.com/DTD/2010/feeds/attributes/}attr"
            ):
                raw_data[attr.get("name", "")] = attr.get("value", "")

            size = int(raw_data.get("size", 0) or 0)
            match_score = self._usenet._match_score(canonical_name, title, aliases)
            raw_data["match_score"] = round(match_score, 3)

            candidate = {
                "display_name": display_name,
                "version": version,
                "publisher": publisher,
                "torrent_url": torrent_url,
                "size": size,
                "raw_data": raw_data,
                "title": title,
            }

            dedup_key = (display_name.lower(), version)
            existing = dedup.get(dedup_key)
            if existing is None or size > existing["size"]:
                dedup[dedup_key] = candidate

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
                    source_origin=c["torrent_url"],
                    raw_data=c["raw_data"],
                )
            )

        return results
