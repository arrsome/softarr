"""qBittorrent Web UI integration client.

Handles communication with a qBittorrent instance for queuing torrent downloads
of approved releases. Supports sending magnet links or .torrent URLs, uploading
.torrent files directly, connection health checks, and queue status retrieval.

All calls use qBittorrent's Web API v2 (available since qBittorrent 4.1). The
client authenticates via a session cookie (POST /api/v2/auth/login) and reuses
the cookie for subsequent requests. Re-authentication is attempted automatically
on HTTP 403 (session expired).

qBittorrent does not natively emit outbound webhooks. Completion detection is
handled by a background polling loop in app/main.py. An optional manual webhook
endpoint is also provided for users who configure qBittorrent's "Run Program on
Torrent Completion" feature.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from softarr.integrations.download_client import (
    AbstractDownloadClient,
    DownloadClientError,
)

logger = logging.getLogger("softarr.qbittorrent")

DEFAULT_TIMEOUT = 30
MAX_RESPONSE_SIZE = 5 * 1024 * 1024  # 5 MB

# Prefix stored in Release.download_client_id to identify qBittorrent jobs.
# SABnzbd nzo_ids never start with this prefix, so the two can coexist safely.
QBIT_HASH_PREFIX = "qbt:"

# qBittorrent torrent states that indicate a completed download
_COMPLETED_STATES = {
    "uploading",
    "stalledup",
    "pausedup",
    "seeding",
    "forcedUP",
    "queuedUP",
}

# qBittorrent torrent states that indicate a failed download
_FAILED_STATES = {"error", "missingFiles"}


@dataclass
class QBittorrentConfig:
    url: str
    username: str
    password: str
    category: str = "software"
    ssl_verify: bool = True
    timeout: int = DEFAULT_TIMEOUT


class QBittorrentError(DownloadClientError):
    """Raised when a qBittorrent API call fails."""

    pass


class QBittorrentClient(AbstractDownloadClient):
    """Client for the qBittorrent Web API v2."""

    def __init__(self, config: QBittorrentConfig):
        self.config = config
        self._cookies: Dict[str, str] = {}
        self._validate_config()

    def _validate_config(self) -> None:
        if not self.config.url:
            raise QBittorrentError("qBittorrent URL is not configured")
        if not self.config.username:
            raise QBittorrentError("qBittorrent username is not configured")
        self.config.url = self.config.url.rstrip("/")

    def _new_client(self) -> httpx.AsyncClient:
        """Create a new httpx client with stored cookies."""
        return httpx.AsyncClient(
            verify=self.config.ssl_verify,
            timeout=self.config.timeout,
            cookies=self._cookies,
        )

    async def _login(self, client: httpx.AsyncClient) -> None:
        """Authenticate and store the session cookie.

        Raises QBittorrentError if credentials are rejected.
        """
        try:
            resp = await client.post(
                f"{self.config.url}/api/v2/auth/login",
                data={
                    "username": self.config.username,
                    "password": self.config.password,
                },
            )
        except httpx.RequestError as exc:
            raise QBittorrentError(f"qBittorrent login request failed: {exc}")

        body = resp.text.strip()
        if body == "Fails.":
            raise QBittorrentError(
                "qBittorrent rejected credentials -- check username and password"
            )
        if body != "Ok.":
            raise QBittorrentError(f"Unexpected qBittorrent login response: {body!r}")

        # Persist the SID cookie for subsequent requests
        self._cookies = dict(client.cookies)
        logger.debug("qBittorrent login successful")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, str]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        retry_auth: bool = True,
    ) -> httpx.Response:
        """Make an authenticated request, re-logging in on 403."""
        async with self._new_client() as client:
            if not self._cookies:
                await self._login(client)

            url = f"{self.config.url}{path}"
            try:
                if method == "GET":
                    resp = await client.get(url, params=params)
                else:
                    resp = await client.post(url, params=params, data=data, files=files)
            except httpx.TimeoutException:
                raise QBittorrentError(
                    f"qBittorrent request timed out after {self.config.timeout}s"
                )
            except httpx.ConnectError:
                raise QBittorrentError(
                    f"Cannot connect to qBittorrent at {self.config.url}"
                )
            except httpx.RequestError as exc:
                raise QBittorrentError(f"qBittorrent request failed: {exc}")

            if resp.status_code == 403 and retry_auth:
                # Session expired -- re-authenticate and retry once
                self._cookies = {}
                async with self._new_client() as client2:
                    await self._login(client2)
                return await self._request(
                    method,
                    path,
                    params=params,
                    data=data,
                    files=files,
                    retry_auth=False,
                )

            if resp.status_code not in (200, 204):
                raise QBittorrentError(
                    f"qBittorrent returned HTTP {resp.status_code} for {path}"
                )

            return resp

    # ------------------------------------------------------------------
    # AbstractDownloadClient implementation
    # ------------------------------------------------------------------

    async def test_connection(self) -> Dict[str, Any]:
        """Test the connection and return qBittorrent version info.

        Returns dict with 'connected' and 'version' on success.
        Raises QBittorrentError on failure.
        """
        resp = await self._request("GET", "/api/v2/app/version")
        version = resp.text.strip()
        logger.info("qBittorrent connection test successful: version=%s", version)
        return {"connected": True, "version": version}

    async def send_url(
        self,
        url: str,
        name: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a magnet link or .torrent URL to qBittorrent.

        Returns a dict with 'ids' containing the torrent infohash prefixed
        with QBIT_HASH_PREFIX so it can be distinguished from SABnzbd job IDs.
        Raises QBittorrentError on failure.
        """
        if not url:
            raise QBittorrentError("Download URL is required")

        # Record timestamp before adding so we can identify the new torrent
        before_ts = int(time.time()) - 2

        data: Dict[str, Any] = {
            "urls": url,
            "category": category or self.config.category,
        }
        if name:
            data["rename"] = name

        await self._request("POST", "/api/v2/torrents/add", data=data)
        logger.info("Sent torrent URL to qBittorrent: %s (name=%s)", url, name)

        # Retrieve the hash of the newly added torrent
        hash_hex = await self._get_hash_after_add(name_hint=name, before_ts=before_ts)
        ids = [f"{QBIT_HASH_PREFIX}{hash_hex}"] if hash_hex else []
        return {"ids": ids}

    async def send_file(
        self,
        file_content: bytes,
        filename: str,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload a .torrent file directly to qBittorrent.

        Returns a dict with 'ids' containing the torrent infohash prefixed
        with QBIT_HASH_PREFIX.
        Raises QBittorrentError on failure.
        """
        if not file_content:
            raise QBittorrentError(".torrent file content is empty")

        before_ts = int(time.time()) - 2

        data: Dict[str, Any] = {"category": category or self.config.category}
        files = {"torrents": (filename, file_content, "application/x-bittorrent")}

        await self._request("POST", "/api/v2/torrents/add", data=data, files=files)
        logger.info("Uploaded .torrent file to qBittorrent: %s", filename)

        hash_hex = await self._get_hash_after_add(
            name_hint=filename, before_ts=before_ts
        )
        ids = [f"{QBIT_HASH_PREFIX}{hash_hex}"] if hash_hex else []
        return {"ids": ids}

    async def get_queue(self) -> Dict[str, Any]:
        """Return the current qBittorrent download queue.

        Returns a normalised dict with 'queue' -> 'slots' list. Each slot has:
        - hash: torrent infohash (without QBIT_HASH_PREFIX)
        - filename: torrent display name
        - percentage: integer percent complete (0-100)
        - size_mb: total size in megabytes
        - status: qBittorrent state string
        """
        resp = await self._request("GET", "/api/v2/torrents/info")
        torrents: List[Dict[str, Any]] = resp.json()
        slots = []
        for t in torrents:
            progress = float(t.get("progress", 0))
            size_bytes = int(t.get("size", 0))
            slots.append(
                {
                    "hash": t.get("hash", ""),
                    "filename": t.get("name", ""),
                    "percentage": round(progress * 100),
                    "size_mb": round(size_bytes / (1024 * 1024), 1),
                    "status": t.get("state", ""),
                }
            )
        return {"queue": {"slots": slots}}

    # ------------------------------------------------------------------
    # Additional helpers used by the completion poller
    # ------------------------------------------------------------------

    async def get_torrent_info(self, hash_hex: str) -> Optional[Dict[str, Any]]:
        """Return info for a single torrent by its infohash.

        Returns None if the torrent is not found in qBittorrent (e.g. it has
        been removed after seeding). Used by the background completion poller.
        """
        resp = await self._request(
            "GET",
            "/api/v2/torrents/info",
            params={"hashes": hash_hex},
        )
        items: List[Dict[str, Any]] = resp.json()
        return items[0] if items else None

    async def _get_hash_after_add(
        self,
        name_hint: Optional[str],
        before_ts: int,
    ) -> Optional[str]:
        """Find the infohash of the most recently added torrent.

        Queries the full torrent list and returns the first torrent whose
        added_on timestamp is >= before_ts. Falls back to matching by name
        hint if multiple torrents were added simultaneously.

        Returns None if no match is found (e.g. the torrent was rejected
        as a duplicate by qBittorrent).
        """
        # Small delay to allow qBittorrent to process the add request
        await asyncio.sleep(0.5)

        resp = await self._request(
            "GET", "/api/v2/torrents/info", params={"filter": "all"}
        )
        torrents: List[Dict[str, Any]] = resp.json()

        # Collect candidates added after our reference timestamp
        candidates = [t for t in torrents if int(t.get("added_on", 0)) >= before_ts]

        if not candidates:
            logger.warning(
                "qBittorrent: could not find newly added torrent "
                "(name_hint=%r, before_ts=%d)",
                name_hint,
                before_ts,
            )
            return None

        # If name hint available, prefer a name match
        if name_hint:
            hint_lower = name_hint.lower()
            for t in candidates:
                if hint_lower in (t.get("name") or "").lower():
                    return t.get("hash")

        # Fall back to most-recently-added candidate
        candidates.sort(key=lambda t: t.get("added_on", 0), reverse=True)
        return candidates[0].get("hash")
