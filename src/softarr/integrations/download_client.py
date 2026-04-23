"""Abstract download client interface.

All download client integrations must implement ``AbstractDownloadClient``
so the rest of the application can interact with any client via the same API.

Current implementations:
  - SABnzbdClient (app/integrations/sabnzbd.py)

Future candidates:
  - NZBGet
  - qBittorrent / Deluge / Transmission
  - Direct HTTP download
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class DownloadClientError(Exception):
    """Raised when a download client operation fails."""


class AbstractDownloadClient(ABC):
    """Abstract interface for download client integrations."""

    @abstractmethod
    async def test_connection(self) -> Dict[str, Any]:
        """Test connectivity to the download client.

        Returns a dict with at least ``{"status": "ok"}`` on success.
        Raises DownloadClientError on failure.
        """

    @abstractmethod
    async def send_url(
        self,
        url: str,
        name: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Queue a download by URL.

        Returns a dict that may include ``ids`` (list of client job IDs).
        Raises DownloadClientError on failure.
        """

    @abstractmethod
    async def send_file(
        self,
        file_content: bytes,
        filename: str,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Queue a download by uploading a file (e.g. an NZB).

        Returns a dict that may include ``ids`` (list of client job IDs).
        Raises DownloadClientError on failure.
        """

    @abstractmethod
    async def get_queue(self) -> Dict[str, Any]:
        """Return the current download queue.

        Returns a dict with at least ``{"queue": {"slots": [...]}}`` where
        each slot has at minimum ``nzo_id``, ``filename``, and ``status``.
        """
