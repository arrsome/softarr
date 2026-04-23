"""Notification history model.

Records every notification attempt (success or failure) across all channels.
Allows users to diagnose delivery problems from the Settings UI.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, String, Text

from softarr.core.database import GUID, Base


def _utcnow():
    return datetime.now(timezone.utc)


class NotificationHistory(Base):
    __tablename__ = "notification_history"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    # Event name: new_release_discovered, release_flagged, download_queued, etc.
    event = Column(String(100), nullable=False)
    # Channel: email | discord | http | apprise
    channel = Column(String(50), nullable=False)
    success = Column(Boolean, nullable=False)
    error_message = Column(Text, nullable=True)
    # Abbreviated payload for display (not the full notification body)
    payload = Column(JSON, default=dict)
    sent_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
