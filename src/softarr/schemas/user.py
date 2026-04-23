"""Pydantic schemas for user management API."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "admin"  # "admin" | "viewer"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("admin", "viewer"):
            raise ValueError("role must be 'admin' or 'viewer'")
        return v


class UserUpdate(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("admin", "viewer"):
            raise ValueError("role must be 'admin' or 'viewer'")
        return v


class AdminPasswordReset(BaseModel):
    new_password: str


class UserResponse(BaseModel):
    id: UUID
    username: str
    role: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    last_login: Optional[datetime] = None
    force_password_change: bool = False

    model_config = {"from_attributes": True}
