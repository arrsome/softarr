"""Web Push subscription model.

Stores browser push subscriptions (endpoint + VAPID keys) for authenticated
users. Each user may have multiple subscriptions (different browsers/devices).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text

from softarr.core.database import GUID, Base


def _utcnow():
    return datetime.now(timezone.utc)


class PushSubscription(Base):
    """A browser Web Push subscription belonging to a user."""

    __tablename__ = "push_subscriptions"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    # Foreign key to users table (nullable to allow orphan cleanup)
    user_id = Column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The push service endpoint URL
    endpoint = Column(Text, nullable=False, unique=True)
    # VAPID public key for this subscription (p256dh)
    p256dh = Column(String(256), nullable=False)
    # Auth secret for the subscription
    auth = Column(String(64), nullable=False)
    # User agent description for display in settings
    user_agent = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
