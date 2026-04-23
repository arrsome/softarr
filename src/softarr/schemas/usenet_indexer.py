import re
from typing import Optional

from pydantic import BaseModel, field_validator

_DEFAULT_CATEGORIES = "4000,4010,4020,4030,4040,4050,4060,4070"
# Indexer names are used as INI section keys and URL path segments.
# Only allow alphanumeric characters, hyphens, underscores, and dots.
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class UsenetIndexerCreate(BaseModel):
    """Request to create a new indexer (Newznab or Torznab)."""

    name: str
    url: str
    api_key: str
    enabled: bool = True
    priority: int = 0
    categories: str = _DEFAULT_CATEGORIES
    type: str = "newznab"  # "newznab" (Usenet/NZB) or "torznab" (torrent)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "Indexer name must contain only letters, numbers, hyphens, underscores, or dots"
            )
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("newznab", "torznab"):
            raise ValueError("Indexer type must be 'newznab' or 'torznab'")
        return v


class UsenetIndexerUpdate(BaseModel):
    """Partial update for an existing indexer."""

    name: Optional[str] = None
    url: Optional[str] = None
    api_key: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    categories: Optional[str] = None
    type: Optional[str] = None


class UsenetIndexerResponse(BaseModel):
    """Indexer returned from the API. The api_key is always masked."""

    name: str
    url: str
    api_key: str  # Masked by the service layer before construction
    enabled: bool
    priority: int
    categories: str = _DEFAULT_CATEGORIES
    type: str = "newznab"
