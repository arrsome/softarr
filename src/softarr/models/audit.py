import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from softarr.core.database import GUID, Base


def _utcnow():
    return datetime.now(timezone.utc)


class ReleaseOverride(Base):
    __tablename__ = "release_overrides"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    release_id = Column(GUID(), ForeignKey("releases.id"), nullable=False)
    overridden_by = Column(String(255), nullable=False)
    override_timestamp = Column(DateTime(timezone=True), default=_utcnow)
    override_reason = Column(Text, nullable=True)

    release = relationship("Release", back_populates="overrides")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50))
    entity_id = Column(GUID())
    user = Column(String(255), nullable=True)
    details = Column(JSON, default=dict)
    timestamp = Column(DateTime(timezone=True), default=_utcnow)
