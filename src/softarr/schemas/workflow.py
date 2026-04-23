from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


class OverrideRequest(BaseModel):
    reason: Optional[str] = None


class WorkflowTransition(BaseModel):
    """Request to move a release between workflow states."""

    target_state: str  # discovered, staged, under_review, approved, rejected
    reason: Optional[str] = None


class StagingAction(BaseModel):
    release_id: str
    action: str  # stage, review, approve, reject, override
    reason: Optional[str] = None


class BulkApproveRequest(BaseModel):
    """Bulk approve multiple releases by ID."""

    release_ids: List[UUID]


class BulkRejectRequest(BaseModel):
    """Bulk reject multiple releases by ID."""

    release_ids: List[UUID]
    reason: Optional[str] = None
