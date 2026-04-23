"""Password history model.

Stores hashed copies of previous passwords for each user so that the
password policy service can prevent reuse.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String

from softarr.core.database import GUID, Base


def _utcnow():
    return datetime.now(timezone.utc)


class PasswordHistory(Base):
    __tablename__ = "password_history"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    password_hash = Column(String(255), nullable=False)
    changed_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
