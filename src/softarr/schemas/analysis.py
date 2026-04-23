from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ReleaseAnalysisResponse(BaseModel):
    id: UUID
    release_id: UUID
    signature_status: Optional[str] = None
    hash_status: Optional[str] = None
    unusual_file_detection: List[str] = Field(default_factory=list)
    suspicious_naming: List[str] = Field(default_factory=list)
    source_trust_score: float = 0.0
    match_quality_score: float = 0.0
    analyzed_at: datetime

    model_config = {"from_attributes": True}
