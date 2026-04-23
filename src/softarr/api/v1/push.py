"""Web Push subscription management endpoints.

Provides:
  POST /subscribe    -- Register a new browser push subscription
  DELETE /unsubscribe -- Remove a push subscription
  GET  /vapid-key    -- Retrieve the VAPID public key for client-side subscription
  POST /generate-keys -- (Admin) Generate new VAPID key pair and save to settings
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.dependencies import require_admin, require_auth
from softarr.core.database import get_db
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.services.push_notification_service import PushNotificationService

logger = logging.getLogger("softarr.api.push")

router = APIRouter()


class SubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str
    user_agent: Optional[str] = None


class UnsubscribeRequest(BaseModel):
    endpoint: str


@router.get("/vapid-key")
async def get_vapid_key(
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_auth),
):
    """Return the VAPID public key needed for browser-side subscription creation.

    The client must use this key when calling ``PushManager.subscribe()``.
    """
    public_key = ini.get("push_vapid_public_key") or ""
    enabled = (ini.get("push_notifications_enabled") or "false").lower() == "true"
    return {"public_key": public_key, "enabled": enabled}


@router.post("/subscribe")
async def subscribe(
    body: SubscribeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_auth),
):
    """Register a browser push subscription for the current user."""
    enabled = (ini.get("push_notifications_enabled") or "false").lower() == "true"
    if not enabled:
        raise HTTPException(
            status_code=503, detail="Push notifications are not enabled"
        )

    try:
        user_id = UUID(user["uid"])
    except KeyError, ValueError:
        raise HTTPException(status_code=401, detail="Invalid session")

    svc = PushNotificationService(db, ini)
    sub = await svc.add_subscription(
        user_id=user_id,
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        user_agent=body.user_agent,
    )
    return {"id": str(sub.id), "status": "subscribed"}


@router.delete("/unsubscribe")
async def unsubscribe(
    body: UnsubscribeRequest,
    db: AsyncSession = Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
    user: dict = Depends(require_auth),
):
    """Remove a push subscription for the current user."""
    try:
        user_id = UUID(user["uid"])
    except KeyError, ValueError:
        raise HTTPException(status_code=401, detail="Invalid session")

    svc = PushNotificationService(db, ini)
    removed = await svc.remove_subscription(body.endpoint, user_id)
    return {"status": "unsubscribed" if removed else "not_found"}


@router.post("/generate-keys")
async def generate_vapid_keys(
    ini: IniSettingsManager = Depends(get_ini_settings),
    _user: dict = Depends(require_admin),
):
    """Generate a new VAPID key pair and save it to settings.

    Warning: existing subscriptions will break if keys are rotated.
    Only generate keys once during initial setup.
    """
    from py_vapid import Vapid01

    vapid = Vapid01()
    vapid.generate_keys()

    # Export keys in the formats pywebpush expects
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    public_key_bytes = vapid.public_key.public_bytes(
        encoding=Encoding.X962,
        format=PublicFormat.UncompressedPoint,
    )
    private_key_pem = vapid.private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=NoEncryption(),
    ).decode("utf-8")

    # Encode public key as Base64url (standard VAPID format)
    import base64

    public_key_b64 = (
        base64.urlsafe_b64encode(public_key_bytes).rstrip(b"=").decode("ascii")
    )

    ini.set("push_vapid_public_key", public_key_b64)
    ini.set("push_vapid_private_key", private_key_pem)

    logger.info("New VAPID key pair generated and saved")
    return {"public_key": public_key_b64, "status": "keys_generated"}
