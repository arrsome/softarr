import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import relationship

from softarr.core.database import GUID, Base


def _utcnow():
    return datetime.now(timezone.utc)


class ReleaseAnalysis(Base):
    __tablename__ = "release_analysis"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    release_id = Column(GUID(), ForeignKey("releases.id"), nullable=False)
    signature_status = Column(String(50))
    hash_status = Column(String(50))
    unusual_file_detection = Column(JSON, default=list)
    suspicious_naming = Column(JSON, default=list)
    source_trust_score = Column(Float, default=0.0)
    match_quality_score = Column(Float, default=0.0)
    analyzed_at = Column(DateTime(timezone=True), default=_utcnow)

    release = relationship("Release", back_populates="analysis")
