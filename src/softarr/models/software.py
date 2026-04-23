import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, String, Text
from sqlalchemy.orm import relationship

from softarr.core.database import GUID, Base


def _utcnow():
    return datetime.now(timezone.utc)


class Software(Base):
    __tablename__ = "software"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    canonical_name = Column(String(255), unique=True, nullable=False)
    aliases = Column(JSON, default=list)
    expected_publisher = Column(String(255), nullable=True)
    supported_os = Column(JSON, default=list)
    architecture = Column(String(50), nullable=True)
    version_format_rules = Column(JSON, default=dict)
    source_preferences = Column(JSON, default=list)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    preferred_adapter = Column(
        String(50), nullable=True
    )  # github, usenet, torznab, or None (auto)

    # ARR-stack additions
    monitored = Column(Boolean, default=True, nullable=False)
    tags = Column(JSON, default=list)  # list of tag strings
    # download_profile: dict with keys preferred_source, min_match_score, auto_approve_threshold
    download_profile = Column(JSON, default=dict)
    last_searched_at = Column(DateTime(timezone=True), nullable=True)

    # Version pinning (migration 0013_version_pin)
    # None = unpinned; dict = {"mode": "exact"|"major"|"disabled", "value": "x.y.z"}
    version_pin = Column(JSON, nullable=True, default=None)
    # Auto-reject rules (migration 0013_version_pin)
    # List of rule strings: "pre_release", "nightly", "portable", "unsigned", "wrong_publisher"
    auto_reject_rules = Column(JSON, nullable=True, default=list)
    # Release type filter (migration 0013_version_pin)
    # List of allowed types: "installer", "archive", "source", "binary"
    # Empty list means all types allowed
    release_type_filter = Column(JSON, nullable=True, default=list)

    releases = relationship(
        "Release", back_populates="software", cascade="all, delete-orphan"
    )
