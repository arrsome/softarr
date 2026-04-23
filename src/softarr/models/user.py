import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, String, text

from softarr.core.database import GUID, Base


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    # Explicit role field: "admin" | "viewer" (supersedes is_admin)
    role = Column(String(20), nullable=True, default="admin")
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    last_login = Column(DateTime(timezone=True), nullable=True)

    # Password policy fields
    password_changed_at = Column(DateTime(timezone=True), nullable=True)
    force_password_change = Column(Boolean, default=False)

    # TOTP 2FA fields (migration 0012_totp)
    totp_secret = Column(String(255), nullable=True)  # encrypted base32 secret
    totp_enabled = Column(Boolean, default=False, nullable=False)

    # Legal disclaimer acceptance (migration 0015_disclaimer_accepted)
    disclaimer_accepted = Column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    disclaimer_accepted_at = Column(DateTime(timezone=True), nullable=True)

    # Language preference (migration 0016_user_language)
    language = Column(String(10), nullable=False, default="en", server_default="en")
