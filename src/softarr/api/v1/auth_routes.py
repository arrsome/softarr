"""Authentication-related API endpoints.

Extends the core auth routes with self-service password change and
password policy validation.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.dependencies import require_auth
from softarr.auth.service import AuthService
from softarr.core.database import get_db
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.services.audit_service import AuditService
from softarr.services.password_policy_service import PasswordPolicyService

router = APIRouter()


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_auth),
):
    """Change the current user's password.

    Validates the current password, enforces policy rules, checks history,
    and logs the change to the audit log.
    """
    if body.new_password != body.confirm_password:
        raise HTTPException(status_code=400, detail="New passwords do not match.")

    auth_service = AuthService(db)
    username = user.get("u", "")
    db_user = await auth_service.get_user_by_username(username)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Verify current password
    from softarr.auth.passwords import verify_password

    if not verify_password(body.current_password, db_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    # Validate against policy
    policy = PasswordPolicyService(db, ini)
    errors = policy.validate_password(body.new_password)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"errors": errors},
        )

    # Check history
    allowed = await policy.check_history(db_user.id, body.new_password)
    if not allowed:
        n = int(ini.get("password_history_count") or "5")
        raise HTTPException(
            status_code=400,
            detail=f"Password was used recently. Choose one not in the last {n} passwords.",
        )

    # Apply change (records old hash in history)
    await auth_service.change_password(
        db_user.id, body.new_password, record_history=True
    )

    # Audit
    audit = AuditService(db)
    await audit.log_action(
        "password_change",
        "user",
        db_user.id,
        user=username,
        details={"reason": "self_service"},
    )

    return {"status": "ok", "message": "Password changed successfully."}


@router.get("/password-policy")
async def get_password_policy(
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
):
    """Return the current password policy rules for client-side display."""
    policy = PasswordPolicyService(None, ini)  # type: ignore[arg-type]
    return {
        "min_length": policy._get_int("password_min_length", 12),
        "require_uppercase": policy._get_bool("password_require_uppercase"),
        "require_numbers": policy._get_bool("password_require_numbers"),
        "require_special": policy._get_bool("password_require_special"),
        "history_count": policy._get_int("password_history_count", 5),
        "max_age_days": policy._get_int("password_max_age_days", 0),
    }
