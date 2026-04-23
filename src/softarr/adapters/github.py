"""GitHub Releases adapter with safety boundaries.

Only makes outbound requests to api.github.com. All responses are
size-limited and time-bounded to prevent SSRF-like abuse if the
adapter logic is later expanded carelessly.
"""

import logging
import re
from typing import Dict, List

import httpx

from softarr.adapters.base import BaseAdapter, ReleaseSearchResult
from softarr.core.config import settings

logger = logging.getLogger("softarr.github")

# Safety limits
REQUEST_TIMEOUT = 15  # seconds
MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_HOSTS = {"api.github.com"}


def _validate_github_url(url: str) -> bool:
    """Only allow requests to api.github.com."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return parsed.hostname in ALLOWED_HOSTS and parsed.scheme == "https"
    except Exception:
        return False


class GitHubAdapter(BaseAdapter):
    name = "GitHub Releases"
    source_type = "github"

    def __init__(self):
        pass

    def _get_headers(self) -> dict:
        """Build request headers, resolving the token from INI then env."""
        from softarr.core.ini_settings import get_ini_settings

        token = get_ini_settings().get("github_token") or settings.GITHUB_TOKEN
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        return headers

    async def _safe_get(
        self, client: httpx.AsyncClient, url: str, **kwargs
    ) -> httpx.Response:
        """GET with URL validation, timeout, and size guard."""
        if not _validate_github_url(url):
            raise ValueError(f"Request blocked: {url} is not an allowed GitHub API URL")

        resp = await client.get(
            url, headers=self._get_headers(), timeout=REQUEST_TIMEOUT, **kwargs
        )

        if len(resp.content) > MAX_RESPONSE_SIZE:
            raise ValueError("GitHub API response exceeded size limit")

        return resp

    async def search_releases(
        self, software: Dict, query: str | None = None
    ) -> List[ReleaseSearchResult]:
        results = []
        owner_repo = self._extract_owner_repo(software.get("canonical_name", ""))

        async with httpx.AsyncClient() as client:
            if not owner_repo:
                search_query = query or software.get("canonical_name", "")
                try:
                    resp = await self._safe_get(
                        client,
                        "https://api.github.com/search/repositories",
                        params={"q": search_query, "per_page": 5},
                    )
                except (httpx.TimeoutException, ValueError) as e:
                    logger.warning("GitHub search failed: %s", e)
                    return results

                if resp.status_code != 200:
                    logger.warning("GitHub search returned %d", resp.status_code)
                    return results

                data = resp.json()
                for item in data.get("items", []):
                    releases_url = (
                        f"https://api.github.com/repos/{item['full_name']}/releases"
                    )
                    try:
                        rel_resp = await self._safe_get(
                            client, releases_url, params={"per_page": 5}
                        )
                    except (httpx.TimeoutException, ValueError) as e:
                        logger.warning("GitHub releases fetch failed: %s", e)
                        continue

                    if rel_resp.status_code == 200:
                        for rel in rel_resp.json():
                            results.append(
                                self._normalize_release(
                                    rel,
                                    software,
                                    publisher=item.get("owner", {}).get("login"),
                                )
                            )
                return results

            # Direct repo lookup
            releases_url = f"https://api.github.com/repos/{owner_repo}/releases"
            try:
                resp = await self._safe_get(
                    client, releases_url, params={"per_page": 10}
                )
            except (httpx.TimeoutException, ValueError) as e:
                logger.warning("GitHub releases fetch failed: %s", e)
                return results

            if resp.status_code == 200:
                for rel in resp.json():
                    results.append(
                        self._normalize_release(
                            rel,
                            software,
                            publisher=software.get("expected_publisher"),
                        )
                    )

        return results

    async def fetch_release_details(self, release_url: str) -> Dict:
        """Fetch details for a specific release. Only allows GitHub API URLs."""
        if not _validate_github_url(release_url):
            return {"error": "URL is not an allowed GitHub API endpoint"}

        async with httpx.AsyncClient() as client:
            try:
                resp = await self._safe_get(client, release_url)
                if resp.status_code == 200:
                    return resp.json()
            except (httpx.TimeoutException, ValueError) as e:
                return {"error": str(e)}

        return {"url": release_url, "error": "Failed to fetch details"}

    @staticmethod
    def _normalize_release(
        rel: Dict, software: Dict, publisher: str = None
    ) -> ReleaseSearchResult:
        # Collect signature asset URLs from release assets
        signature_exts = (".sig", ".asc", ".sigstore", ".bundle")
        signature_assets = [
            asset["browser_download_url"]
            for asset in rel.get("assets", [])
            if asset.get("browser_download_url", "").lower().endswith(signature_exts)
        ]
        raw = dict(rel)
        if signature_assets:
            raw["signature_assets"] = signature_assets
        # Preserve release notes from the GitHub release body
        if rel.get("body"):
            raw["release_notes"] = rel["body"]

        return ReleaseSearchResult(
            name=rel.get("name") or rel["tag_name"],
            version=rel["tag_name"],
            supported_os=software.get("supported_os", ["windows", "linux", "macos"]),
            architecture=None,
            publisher=publisher,
            source_type="github",
            source_origin=rel["html_url"],
            raw_data=raw,
        )

    @staticmethod
    def _extract_owner_repo(name: str) -> str | None:
        match = re.match(r"([a-zA-Z0-9._-]+)/([a-zA-Z0-9._-]+)", name)
        return match.group(0) if match else None
