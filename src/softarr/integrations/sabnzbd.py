"""SABnzbd integration client.

Handles communication with a SABnzbd instance for queuing downloads
of approved releases. Supports sending NZB URLs or direct file uploads,
connection health checks, and queue status retrieval.

All calls require a configured base URL and API key. The client validates
configuration before any operation and raises clear errors on failure.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from softarr.integrations.download_client import (
    AbstractDownloadClient,
    DownloadClientError,
)

logger = logging.getLogger("softarr.sabnzbd")

# Timeout for SABnzbd API calls (seconds)
DEFAULT_TIMEOUT = 30
MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5 MB


@dataclass
class SABnzbdConfig:
    url: str
    api_key: str
    category: str = "software"
    ssl_verify: bool = True
    timeout: int = DEFAULT_TIMEOUT


class SABnzbdError(DownloadClientError):
    """Raised when a SABnzbd API call fails."""

    pass


class SABnzbdClient(AbstractDownloadClient):
    """Client for the SABnzbd HTTP API."""

    def __init__(self, config: SABnzbdConfig):
        self.config = config
        self._validate_config()

    def _validate_config(self) -> None:
        if not self.config.url:
            raise SABnzbdError("SABnzbd URL is not configured")
        if not self.config.api_key:
            raise SABnzbdError("SABnzbd API key is not configured")
        # Normalize URL
        self.config.url = self.config.url.rstrip("/")

    def _base_params(self) -> Dict[str, str]:
        return {
            "apikey": self.config.api_key,
            "output": "json",
        }

    async def _request(
        self, params: Dict[str, str], method: str = "GET"
    ) -> Dict[str, Any]:
        """Make an authenticated request to the SABnzbd API."""
        all_params = {**self._base_params(), **params}
        url = f"{self.config.url}/api"

        try:
            async with httpx.AsyncClient(
                verify=self.config.ssl_verify,
                timeout=self.config.timeout,
            ) as client:
                if method == "GET":
                    resp = await client.get(url, params=all_params)
                else:
                    resp = await client.post(url, data=all_params)

                if resp.status_code != 200:
                    raise SABnzbdError(f"SABnzbd returned HTTP {resp.status_code}")

                # Guard against oversized responses
                if len(resp.content) > MAX_RESPONSE_SIZE:
                    raise SABnzbdError("SABnzbd response exceeded size limit")

                data = resp.json()

                # SABnzbd returns {"status": false, "error": "..."} on failure
                if isinstance(data, dict) and data.get("status") is False:
                    raise SABnzbdError(
                        f"SABnzbd error: {data.get('error', 'Unknown error')}"
                    )

                return data

        except httpx.TimeoutException:
            raise SABnzbdError(
                f"SABnzbd request timed out after {self.config.timeout}s"
            )
        except httpx.ConnectError:
            raise SABnzbdError(f"Cannot connect to SABnzbd at {self.config.url}")
        except httpx.RequestError as e:
            raise SABnzbdError(f"SABnzbd request failed: {e}")

    async def test_connection(self) -> Dict[str, Any]:
        """Test the connection and return SABnzbd version info.

        Returns dict with 'version', 'status', etc. on success.
        Raises SABnzbdError on failure.
        """
        data = await self._request({"mode": "version"})
        logger.info("SABnzbd connection test successful: %s", data)
        return {"connected": True, "version": data.get("version", "unknown")}

    async def get_queue(self) -> Dict[str, Any]:
        """Get the current download queue status."""
        return await self._request({"mode": "queue", "limit": 20})

    async def get_categories(self) -> list:
        """Get available categories from SABnzbd."""
        data = await self._request({"mode": "get_cats"})
        return data.get("categories", [])

    async def send_url(
        self,
        url: str,
        name: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send an NZB URL to SABnzbd for download.

        This is the primary method for queuing a release after approval.
        The URL should point to an NZB file or a direct download link
        that SABnzbd can process.

        Args:
            url: The NZB or download URL to send.
            name: Optional display name for the queue entry.
            category: Override category (defaults to config category).

        Returns:
            SABnzbd API response with queue status.
        """
        if not url:
            raise SABnzbdError("Download URL is required")

        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            raise SABnzbdError("URL must start with http:// or https://")

        params = {
            "mode": "addurl",
            "name": url,
            "cat": category or self.config.category,
        }
        if name:
            params["nzbname"] = name

        data = await self._request(params, method="POST")
        logger.info("Sent URL to SABnzbd: %s (name=%s)", url, name)
        return data

    async def send_file(
        self,
        file_content: bytes,
        filename: str,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload raw NZB file content to SABnzbd (implements AbstractDownloadClient).

        Args:
            nzb_content: Raw NZB XML bytes.
            filename: Filename for the upload.
            category: Override category.
        """
        if not file_content:
            raise SABnzbdError("NZB content is empty")

        all_params = self._base_params()
        all_params["mode"] = "addfile"
        all_params["cat"] = category or self.config.category

        url = f"{self.config.url}/api"

        try:
            async with httpx.AsyncClient(
                verify=self.config.ssl_verify,
                timeout=self.config.timeout,
            ) as client:
                resp = await client.post(
                    url,
                    data=all_params,
                    files={"nzbfile": (filename, file_content, "application/x-nzb")},
                )

                if resp.status_code != 200:
                    raise SABnzbdError(f"SABnzbd returned HTTP {resp.status_code}")

                data = resp.json()
                if isinstance(data, dict) and data.get("status") is False:
                    raise SABnzbdError(
                        f"SABnzbd error: {data.get('error', 'Unknown error')}"
                    )

                logger.info("Uploaded NZB to SABnzbd: %s", filename)
                return data

        except httpx.TimeoutException:
            raise SABnzbdError("SABnzbd upload timed out")
        except httpx.RequestError as e:
            raise SABnzbdError(f"SABnzbd upload failed: {e}")

    # Backwards-compatible alias
    async def send_nzb_content(
        self,
        nzb_content: bytes,
        filename: str,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Alias for send_file (backwards compatibility)."""
        return await self.send_file(nzb_content, filename, category)
