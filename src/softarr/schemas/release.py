import math
from datetime import datetime
from typing import Any, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from softarr.models.release import FlagStatus, TrustStatus, WorkflowState


class ReleaseBase(BaseModel):
    name: str
    version: str
    supported_os: List[str] = Field(default_factory=list)
    architecture: Optional[str] = None
    publisher: Optional[str] = None
    source_type: str
    source_origin: Optional[str] = None
    confidence_score: float = 0.0


class ReleaseResponse(ReleaseBase):
    id: UUID
    software_id: UUID
    trust_status: TrustStatus
    flag_status: FlagStatus
    workflow_state: WorkflowState
    workflow_changed_at: Optional[datetime] = None
    workflow_changed_by: Optional[str] = None
    flag_reasons: List[str] = Field(default_factory=list)
    unusual_files: List[str] = Field(default_factory=list)
    suspicious_patterns: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    software_name: Optional[str] = None

    model_config = {"from_attributes": True}

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        instance = super().model_validate(obj, *args, **kwargs)
        # Only read the software relationship if it was eagerly loaded -- accessing it
        # otherwise triggers a lazy load which fails outside an async greenlet context.
        if hasattr(obj, "__dict__"):
            sw = obj.__dict__.get("software")
            if sw is not None:
                instance.software_name = sw.canonical_name
        return instance


class ReleaseDiff(BaseModel):
    """A single field difference between two releases."""

    field: str
    a_value: Any
    b_value: Any


class ReleaseCompareResponse(BaseModel):
    """Side-by-side comparison of two releases with a diff summary."""

    release_a: ReleaseResponse
    release_b: ReleaseResponse
    differences: List[ReleaseDiff]
    newer_version: Literal["a", "b", "equal"]
    recommendation: str


class PaginatedReleaseResponse(BaseModel):
    items: List[ReleaseResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    @classmethod
    def build(
        cls,
        items: List[ReleaseResponse],
        total: int,
        page: int,
        page_size: int,
    ) -> "PaginatedReleaseResponse":
        total_pages = math.ceil(total / page_size) if page_size > 0 else 0
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
