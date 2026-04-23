"""User management API.

Provides CRUD operations for user accounts. All endpoints require admin role.

Endpoints:
  GET  /api/v1/users/              -- list all users
  POST /api/v1/users/              -- create a user
  PATCH /api/v1/users/{id}         -- update role or active status
  DELETE /api/v1/users/{id}        -- deactivate a user
  POST /api/v1/users/{id}/reset-password -- admin password reset
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.dependencies import require_admin
from softarr.auth.service import AuthService
from softarr.core.database import get_db
from softarr.core.i18n import SUPPORTED_LANGUAGES
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.schemas.user import (
    AdminPasswordReset,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from softarr.services.audit_service import AuditService
from softarr.services.password_policy_service import PasswordPolicyService

router = APIRouter()


@router.get("/", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    """List all user accounts."""
    service = AuthService(db)
    users = await service.list_users()
    return [
        UserResponse(
            id=u.id,
            username=u.username,
            role=u.role or ("admin" if u.is_admin else "viewer"),
            is_active=u.is_active,
            is_admin=u.is_admin,
            created_at=u.created_at,
            last_login=u.last_login,
            force_password_change=bool(u.force_password_change),
        )
        for u in users
    ]


@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    admin_user: dict = Depends(require_admin),
):
    """Create a new user account."""
    # Validate password against policy
    policy_svc = PasswordPolicyService(ini)
    errors = policy_svc.validate(body.password)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    service = AuthService(db)
    existing = await service.get_user_by_username(body.username)
    if existing:
        raise HTTPException(
            status_code=409, detail=f"Username already exists: {body.username}"
        )

    user = await service.create_user(
        username=body.username,
        password=body.password,
        is_admin=body.role == "admin",
        role=body.role,
    )

    audit = AuditService(db)
    await audit.log_action(
        "user_created",
        "user",
        user.id,
        user=admin_user.get("u", "admin"),
        details={"username": user.username, "role": user.role},
    )

    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role or body.role,
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at,
        last_login=user.last_login,
        force_password_change=bool(user.force_password_change),
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin_user: dict = Depends(require_admin),
):
    """Update a user's role or active status."""
    service = AuthService(db)
    user = await service.update_user(user_id, role=body.role, is_active=body.is_active)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    audit = AuditService(db)
    await audit.log_action(
        "user_updated",
        "user",
        user_id,
        user=admin_user.get("u", "admin"),
        details={"role": body.role, "is_active": body.is_active},
    )

    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role or ("admin" if user.is_admin else "viewer"),
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at,
        last_login=user.last_login,
        force_password_change=bool(user.force_password_change),
    )


@router.delete("/{user_id}", status_code=204)
async def deactivate_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin_user: dict = Depends(require_admin),
):
    """Deactivate a user account. Refuses if it would leave zero active admins."""
    service = AuthService(db)
    try:
        deleted = await service.deactivate_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")

    audit = AuditService(db)
    await audit.log_action(
        "user_deactivated",
        "user",
        user_id,
        user=admin_user.get("u", "admin"),
    )


@router.post("/{user_id}/reset-password", status_code=204)
async def reset_user_password(
    user_id: UUID,
    body: AdminPasswordReset,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    admin_user: dict = Depends(require_admin),
):
    """Reset a user's password (admin override -- does not require old password)."""
    policy_svc = PasswordPolicyService(ini)
    errors = policy_svc.validate(body.new_password)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    service = AuthService(db)
    success = await service.admin_reset_password(user_id, body.new_password)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    audit = AuditService(db)
    await audit.log_action(
        "user_password_reset",
        "user",
        user_id,
        user=admin_user.get("u", "admin"),
    )


@router.post("/{user_id}/disable-2fa", status_code=204)
async def admin_disable_2fa(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin_user: dict = Depends(require_admin),
):
    """Admin override -- disable TOTP 2FA for any user (e.g. account recovery)."""
    service = AuthService(db)
    success = await service.disable_totp(user_id)
    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    audit = AuditService(db)
    await audit.log_action(
        "user_2fa_disabled",
        "user",
        user_id,
        user=admin_user.get("u", "admin"),
    )


# ---------------------------------------------------------------------------
# Language preference endpoints
# ---------------------------------------------------------------------------


class LanguageUpdate(BaseModel):
    language: str


@router.get("/me/language")
async def get_my_language(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Return the current user's language preference."""
    service = AuthService(db)
    db_user = await service.get_user_by_id(UUID(user["uid"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"language": getattr(db_user, "language", "en") or "en"}


@router.put("/me/language", status_code=204)
async def set_my_language(
    body: LanguageUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Update the current user's language preference.

    The language code must be one of the supported locales. Changes take
    effect on the next page load.
    """
    if body.language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language: {body.language!r}. "
            f"Supported: {sorted(SUPPORTED_LANGUAGES)}",
        )

    service = AuthService(db)
    db_user = await service.get_user_by_id(UUID(user["uid"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    db_user.language = body.language
    await db.commit()
