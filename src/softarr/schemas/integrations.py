from typing import Literal, Optional

from pydantic import BaseModel


class SABnzbdConfigUpdate(BaseModel):
    """Request to update SABnzbd connection settings."""

    url: str
    api_key: str
    category: str = "software"
    ssl_verify: bool = True
    timeout: int = 30


class SABnzbdSendRequest(BaseModel):
    """Request to send a release to SABnzbd."""

    release_id: str
    download_url: Optional[str] = None  # Override; defaults to source_origin
    category: Optional[str] = None  # Per-download category override for SABnzbd


class ActionRequest(BaseModel):
    """Generic action request for the action execution service."""

    release_id: str
    action: str  # send_to_sabnzbd, export_manifest
    download_url: Optional[str] = None


class QBittorrentConfigUpdate(BaseModel):
    """Request to update qBittorrent connection settings."""

    url: str
    username: str
    password: str
    category: str = "software"
    ssl_verify: bool = True
    timeout: int = 30


class QBittorrentSendRequest(BaseModel):
    """Request to send a release to qBittorrent."""

    release_id: str
    download_url: Optional[str] = None  # Override; defaults to source_origin
    category: Optional[str] = None


class ActiveClientUpdate(BaseModel):
    """Request to change the active download client."""

    client: Literal["sabnzbd", "qbittorrent"]
