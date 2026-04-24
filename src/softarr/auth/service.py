"""Auth service -- user creation, login validation, bootstrap.

On first startup, if no users exist in the DB, a default admin user is
created using ADMIN_USERNAME and ADMIN_DEFAULT_PASSWORD (default: "admin").
A loud console warning is printed. The admin must change this password
immediately via Settings > Users.

To use a pre-hashed password instead, set ADMIN_PASSWORD_HASH in the
environment; that path skips the default entirely.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.passwords import hash_password, verify_password
from softarr.core.config import settings
from softarr.core.logging import logger
from softarr.models.user import User

_log = logging.getLogger("softarr.auth.service")


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_user_by_username(self, username: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: UUID) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def authenticate(self, username: str, password: str) -> Optional[User]:
        """Validate credentials and return user or None."""
        user = await self.get_user_by_username(username)
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        user.last_login = datetime.now(timezone.utc)
        await self.db.commit()
        return user

    async def list_users(self) -> List[User]:
        """Return all users ordered by username."""
        result = await self.db.execute(select(User).order_by(User.username))
        return list(result.scalars().all())

    async def create_user(
        self,
        username: str,
        password: str,
        is_admin: bool = False,
        role: str = "admin",
    ) -> User:
        user = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=is_admin,
            role=role,
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def update_user(
        self,
        user_id: UUID,
        role: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[User]:
        """Update a user's role or active status."""
        user = await self.get_user_by_id(user_id)
        if not user:
            return None
        if role is not None:
            user.role = role
            user.is_admin = role == "admin"
        if is_active is not None:
            user.is_active = is_active
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def deactivate_user(self, user_id: UUID) -> bool:
        """Deactivate a user. Refuses if it would leave zero active admins.

        Returns True on success, raises ValueError if the guard triggers.
        """
        user = await self.get_user_by_id(user_id)
        if not user:
            return False
        if not user.is_active:
            return True  # already inactive

        # Guard: never leave zero active admins
        if (user.role or ("admin" if user.is_admin else "viewer")) == "admin":
            result = await self.db.execute(
                select(func.count(User.id)).where(
                    User.is_active == True,  # noqa: E712
                    User.role == "admin",
                    User.id != user_id,
                )
            )
            remaining_admins = result.scalar_one() or 0
            if remaining_admins == 0:
                raise ValueError(
                    "Cannot deactivate the last active admin. Create another admin first."
                )

        user.is_active = False
        await self.db.commit()
        return True

    async def admin_reset_password(self, user_id: UUID, new_password: str) -> bool:
        """Reset a user's password without requiring their old password.

        Does not record password history (admin override action).
        """
        return await self.change_password(user_id, new_password, record_history=False)

    async def change_password(
        self,
        user_id: UUID,
        new_password: str,
        record_history: bool = True,
    ) -> bool:
        """Hash and save a new password for the user.

        If record_history is True, the old hash is stored in password_history
        before being replaced so future reuse checks can find it.
        """
        from datetime import datetime, timezone

        from softarr.models.password_history import PasswordHistory

        user = await self.get_user_by_id(user_id)
        if not user:
            return False

        if record_history and user.password_hash:
            # Save the current hash before overwriting
            history_record = PasswordHistory(
                user_id=user_id,
                password_hash=user.password_hash,
            )
            self.db.add(history_record)

        user.password_hash = hash_password(new_password)
        user.password_changed_at = datetime.now(timezone.utc)
        user.force_password_change = False
        await self.db.commit()
        return True

    # ------------------------------------------------------------------
    # TOTP 2FA
    # ------------------------------------------------------------------

    async def enable_totp(self, user_id: UUID) -> str:
        """Generate a new TOTP secret, store it (signed), mark 2FA enabled.

        Returns the raw base32 secret so the caller can display/QR-encode it.
        The secret must be confirmed with a valid TOTP code via the enrolment
        flow before being treated as active -- but we store it immediately so
        the verify step can check against it.
        """
        from softarr.auth.totp import encrypt_secret, generate_totp_secret

        user = await self.get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")
        raw_secret = generate_totp_secret()
        user.totp_secret = encrypt_secret(raw_secret)
        user.totp_enabled = False  # only set True after verify step
        await self.db.commit()
        return raw_secret

    async def confirm_totp_enrolment(self, user_id: UUID, code: str) -> bool:
        """Verify the TOTP code and, if correct, mark totp_enabled = True.

        Returns True on success, False if the code was wrong.
        """
        from softarr.auth.totp import verify_totp_code

        user = await self.get_user_by_id(user_id)
        if not user:
            _log.warning(
                "TOTP enrolment confirm failed: user not found user_id=%s", user_id
            )
            return False
        if not user.totp_secret:
            _log.warning(
                "TOTP enrolment confirm failed: no totp_secret stored user_id=%s "
                "totp_enabled=%s",
                user_id,
                user.totp_enabled,
            )
            return False
        if not verify_totp_code(user.totp_secret, code):
            _log.warning(
                "TOTP enrolment confirm failed: code rejected user_id=%s "
                "code_len=%d digits_only=%s",
                user_id,
                len(code),
                code.isdigit(),
            )
            return False
        user.totp_enabled = True
        await self.db.commit()
        _log.info("TOTP enrolment confirmed user_id=%s", user_id)
        return True

    async def disable_totp(self, user_id: UUID) -> bool:
        """Disable 2FA for a user, clearing the stored secret."""
        user = await self.get_user_by_id(user_id)
        if not user:
            return False
        user.totp_secret = None
        user.totp_enabled = False
        await self.db.commit()
        return True

    async def verify_totp(self, user_id: UUID, code: str) -> bool:
        """Verify a TOTP code for an already-enrolled user during login."""
        from softarr.auth.totp import verify_totp_code

        user = await self.get_user_by_id(user_id)
        if not user:
            _log.warning("TOTP login verify failed: user not found user_id=%s", user_id)
            return False
        if not user.totp_enabled:
            _log.warning(
                "TOTP login verify failed: totp_enabled=False user_id=%s", user_id
            )
            return False
        if not user.totp_secret:
            _log.warning(
                "TOTP login verify failed: no totp_secret stored user_id=%s", user_id
            )
            return False
        result = verify_totp_code(user.totp_secret, code)
        if not result:
            _log.warning(
                "TOTP login verify failed: code rejected user_id=%s "
                "code_len=%d digits_only=%s",
                user_id,
                len(code),
                code.isdigit(),
            )
        return result

    # ------------------------------------------------------------------
    # Legal disclaimer
    # ------------------------------------------------------------------

    async def accept_disclaimer(self, user_id: UUID) -> bool:
        """Record that the user has accepted the legal disclaimer.

        Sets ``disclaimer_accepted = True`` and ``disclaimer_accepted_at`` to
        the current UTC timestamp. Returns False if the user is not found.
        """
        user = await self.get_user_by_id(user_id)
        if not user:
            return False
        user.disclaimer_accepted = True
        user.disclaimer_accepted_at = datetime.now(timezone.utc)
        await self.db.commit()
        return True

    async def user_count(self) -> int:
        result = await self.db.execute(select(User))
        return len(result.scalars().all())

    async def bootstrap_admin(self) -> Optional[str]:
        """Create default admin if no users exist.

        Returns the generated password so it can be printed to the console,
        or None if users already exist.
        """
        count = await self.user_count()
        if count > 0:
            return None

        password = settings.ADMIN_DEFAULT_PASSWORD
        if settings.ADMIN_PASSWORD_HASH:
            # Use pre-configured hash from env
            user = User(
                username=settings.ADMIN_USERNAME,
                password_hash=settings.ADMIN_PASSWORD_HASH,
                is_admin=True,
            )
            self.db.add(user)
            await self.db.commit()
            logger.info(
                "Admin user '%s' created from ADMIN_PASSWORD_HASH env var",
                settings.ADMIN_USERNAME,
            )
            return None

        await self.create_user(
            username=settings.ADMIN_USERNAME,
            password=password,
            is_admin=True,
        )
        return password
