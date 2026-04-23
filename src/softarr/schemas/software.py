import math
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DownloadProfile(BaseModel):
    """Per-software download preferences."""

    preferred_source: Optional[str] = None  # github, usenet, torznab, or None (auto)
    min_match_score: float = 0.5  # minimum fuzzy match score to consider
    auto_approve_threshold: float = (
        0.0  # confidence >= this value -> auto-approve (0 = disabled)
    )


class SoftwareBase(BaseModel):
    canonical_name: str
    aliases: List[str] = Field(default_factory=list)
    expected_publisher: Optional[str] = None
    supported_os: List[str] = Field(default_factory=list)
    architecture: Optional[str] = None
    version_format_rules: Dict = Field(default_factory=dict)
    source_preferences: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class SoftwareCreate(SoftwareBase):
    monitored: bool = True
    tags: List[str] = Field(default_factory=list)
    download_profile: Dict[str, Any] = Field(default_factory=dict)
    version_pin: Optional[Dict[str, Any]] = None
    auto_reject_rules: List[str] = Field(default_factory=list)
    release_type_filter: List[str] = Field(default_factory=list)


class SoftwareUpdate(BaseModel):
    canonical_name: Optional[str] = None
    aliases: Optional[List[str]] = None
    expected_publisher: Optional[str] = None
    supported_os: Optional[List[str]] = None
    architecture: Optional[str] = None
    version_format_rules: Optional[Dict] = None
    source_preferences: Optional[List[str]] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    preferred_adapter: Optional[str] = None  # github, usenet, torznab, or None (auto)
    monitored: Optional[bool] = None
    tags: Optional[List[str]] = None
    download_profile: Optional[Dict[str, Any]] = None
    version_pin: Optional[Dict[str, Any]] = None
    auto_reject_rules: Optional[List[str]] = None
    release_type_filter: Optional[List[str]] = None


class SoftwareResponse(SoftwareBase):
    id: UUID
    is_active: bool
    preferred_adapter: Optional[str] = None
    monitored: bool = True
    tags: List[str] = Field(default_factory=list)
    download_profile: Dict[str, Any] = Field(default_factory=dict)
    last_searched_at: Optional[datetime] = None
    version_pin: Optional[Dict[str, Any]] = None
    auto_reject_rules: List[str] = Field(default_factory=list)
    release_type_filter: List[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PaginatedSoftwareResponse(BaseModel):
    """Paginated wrapper for software list responses."""

    items: List[SoftwareResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def build(
        cls,
        items: List[SoftwareResponse],
        total: int,
        page: int,
        page_size: int,
    ) -> "PaginatedSoftwareResponse":
        total_pages = math.ceil(total / page_size) if page_size > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
