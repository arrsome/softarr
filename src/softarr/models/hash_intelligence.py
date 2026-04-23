"""Hash intelligence model.

Stores per-release hash lookup results from multiple sources (VirusTotal, NSRL,
CIRCL hashlookup, MalwareBazaar, MISP warninglists, vendor checksums). Enables
historical tracking, verdict aggregation, and scheduled rechecks.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import relationship

from softarr.core.database import GUID, Base


def _utcnow():
    return datetime.now(timezone.utc)


class HashIntelligence(Base):
    __tablename__ = "hash_intelligence"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    release_id = Column(
        GUID(),
        ForeignKey("releases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Hash values (at least sha256 should be present)
    sha256 = Column(String(64), nullable=True)
    sha1 = Column(String(40), nullable=True)
    md5 = Column(String(32), nullable=True)

    # Source identifier: virustotal | nsrl | circl | malwarebazaar | misp | vendor
    source = Column(String(50), nullable=False)

    # Verdict: known_good | known_bad | unknown | vendor_matched | signature_verified
    verdict = Column(String(50), nullable=False, default="unknown")

    # Confidence score 0.0-1.0
    confidence = Column(Float, nullable=True)

    # Raw API response for audit/debugging
    raw_response = Column(JSON, nullable=True)

    # Timing
    checked_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    algorithm = Column(String(20), nullable=False, default="sha256")
    recheck_after = Column(DateTime(timezone=True), nullable=True)

    # Relationship back to release
    release = relationship("Release", back_populates="hash_intelligence")
