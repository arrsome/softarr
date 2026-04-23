import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from softarr.core.database import GUID, Base


class TrustStatus(enum.Enum):
    UNVERIFIED = "unverified"
    DEVELOPER_VERIFIED = "developer_verified"
    ADMIN_VERIFIED = "admin_verified"


class FlagStatus(enum.Enum):
    NONE = "none"
    WARNING = "warning"
    RESTRICTED = "restricted"
    BLOCKED = "blocked"


class WorkflowState(enum.Enum):
    DISCOVERED = "discovered"
    STAGED = "staged"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    QUEUED_FOR_DOWNLOAD = "queued_for_download"
    DOWNLOADED = "downloaded"
    DOWNLOAD_FAILED = "download_failed"


def _utcnow():
    return datetime.now(timezone.utc)


class Release(Base):
    __tablename__ = "releases"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    software_id = Column(GUID(), ForeignKey("software.id"), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(String(100), nullable=False)
    supported_os = Column(JSON, default=list)
    architecture = Column(String(50), nullable=True)
    publisher = Column(String(255), nullable=True)
    source_type = Column(String(50), nullable=False)
    source_origin = Column(String(500), nullable=True)
    confidence_score = Column(Float, default=0.0)
    trust_status = Column(SQLEnum(TrustStatus), default=TrustStatus.UNVERIFIED)
    flag_status = Column(SQLEnum(FlagStatus), default=FlagStatus.NONE)
    flag_reasons = Column(JSON, default=list)
    unusual_files = Column(JSON, default=list)
    suspicious_patterns = Column(JSON, default=list)

    # Explicit workflow state -- replaces flag-derived staging
    workflow_state = Column(
        SQLEnum(WorkflowState), default=WorkflowState.DISCOVERED, nullable=False
    )
    workflow_changed_at = Column(DateTime(timezone=True), default=_utcnow)
    workflow_changed_by = Column(String(255), nullable=True)

    # SABnzbd job ID for reliable completion matching via webhook
    download_client_id = Column(String(255), nullable=True)
    # Release notes/changelog text (from GitHub Releases body or other sources)
    release_notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    software = relationship("Software", back_populates="releases")
    analysis = relationship(
        "ReleaseAnalysis",
        uselist=False,
        back_populates="release",
        cascade="all, delete-orphan",
    )
    overrides = relationship(
        "ReleaseOverride",
        back_populates="release",
        cascade="all, delete-orphan",
    )
    hash_intelligence = relationship(
        "HashIntelligence",
        back_populates="release",
        cascade="all, delete-orphan",
    )
