"""Password policy enforcement service.

Validates new passwords against the configured policy rules and checks
password history to prevent reuse.
"""

import re
from typing import List
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.passwords import verify_password
from softarr.core.ini_settings import IniSettingsManager
from softarr.models.password_history import PasswordHistory


class PasswordPolicyService:
    def __init__(self, db: AsyncSession, ini: IniSettingsManager):
        self.db = db
        self.ini = ini

    def _get_bool(self, key: str) -> bool:
        return (self.ini.get(key) or "false").lower() == "true"

    def _get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.ini.get(key) or str(default))
        except ValueError, TypeError:
            return default

    def validate_password(self, password: str) -> List[str]:
        """Validate a new password against policy rules.

        Returns a list of human-readable error messages. An empty list
        means the password is valid.
        """
        errors: List[str] = []

        min_length = self._get_int("password_min_length", 12)
        if len(password) < min_length:
            errors.append(f"Password must be at least {min_length} characters long.")

        if self._get_bool("password_require_uppercase"):
            if not re.search(r"[A-Z]", password):
                errors.append("Password must contain at least one uppercase letter.")

        if self._get_bool("password_require_numbers"):
            if not re.search(r"\d", password):
                errors.append("Password must contain at least one number.")

        if self._get_bool("password_require_special"):
            if not re.search(r"[^a-zA-Z0-9]", password):
                errors.append("Password must contain at least one special character.")

        return errors

    async def check_history(self, user_id: UUID, new_password: str) -> bool:
        """Return True if the new password is allowed (not in recent history).

        Checks the last N passwords as configured by password_history_count.
        """
        n = self._get_int("password_history_count", 5)
        if n <= 0:
            return True

        result = await self.db.execute(
            select(PasswordHistory)
            .where(PasswordHistory.user_id == user_id)
            .order_by(PasswordHistory.changed_at.desc())
            .limit(n)
        )
        history = result.scalars().all()

        for record in history:
            if verify_password(new_password, record.password_hash):
                return False
        return True

    async def record_password(self, user_id: UUID, password_hash: str) -> None:
        """Store a password hash in history for future reuse checks."""
        record = PasswordHistory(
            user_id=user_id,
            password_hash=password_hash,
        )
        self.db.add(record)
        await self.db.commit()
