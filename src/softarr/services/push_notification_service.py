"""Web Push notification service.

Sends browser push notifications to registered subscribers using the
VAPID protocol. Requires pywebpush and py-vapid packages.

VAPID keys must be generated and stored in softarr.ini:
  push_vapid_public_key   -- Base64url-encoded uncompressed public key
  push_vapid_private_key  -- Base64url-encoded private key
  push_vapid_claims_sub   -- Contact email: mailto:admin@example.com

Usage::

    service = PushNotificationService(db, ini)
    await service.send_to_all_subscribers(
        title="New staging item",
        body="VLC 3.0.21 is ready for review",
        tag="staging",
        url="/releases",
    )
"""

import json
import logging
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.core.ini_settings import IniSettingsManager
from softarr.models.push_subscription import PushSubscription

logger = logging.getLogger("softarr.push_notifications")


def generate_vapid_keys() -> dict:
    """Generate a new VAPID key pair.

    Returns a dict with ``public_key`` and ``private_key`` as Base64url strings.
    Intended for use during initial setup.
    """
    from py_vapid import Vapid01

    vapid = Vapid01()
    vapid.generate_keys()
    return {
        "public_key": vapid.public_key.public_bytes(
            encoding=__import__(
                "cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]
            ).Encoding.X962,
            format=__import__(
                "cryptography.hazmat.primitives.serialization",
                fromlist=["PublicFormat"],
            ).PublicFormat.UncompressedPoint,
        ).hex(),
        "private_key": vapid.private_key.private_bytes(
            encoding=__import__(
                "cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]
            ).Encoding.PEM,
            format=__import__(
                "cryptography.hazmat.primitives.serialization",
                fromlist=["PrivateFormat"],
            ).PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=__import__(
                "cryptography.hazmat.primitives.serialization",
                fromlist=["NoEncryption"],
            ).NoEncryption(),
        ).decode("utf-8"),
    }


class PushNotificationService:
    def __init__(self, db: AsyncSession, ini: IniSettingsManager) -> None:
        self.db = db
        self.ini = ini

    def is_enabled(self) -> bool:
        return (self.ini.get("push_notifications_enabled") or "false").lower() == "true"

    async def get_subscriptions_for_user(self, user_id: UUID) -> List[PushSubscription]:
        """Return all push subscriptions for a user."""
        result = await self.db.execute(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        )
        return list(result.scalars().all())

    async def add_subscription(
        self,
        user_id: UUID,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: Optional[str] = None,
    ) -> PushSubscription:
        """Register a new push subscription for a user.

        If the endpoint already exists, updates the keys (browser may rotate them).
        """
        result = await self.db.execute(
            select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.p256dh = p256dh
            existing.auth = auth
            existing.user_agent = user_agent
            await self.db.commit()
            return existing

        sub = PushSubscription(
            user_id=user_id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            user_agent=user_agent,
        )
        self.db.add(sub)
        await self.db.commit()
        await self.db.refresh(sub)
        return sub

    async def remove_subscription(self, endpoint: str, user_id: UUID) -> bool:
        """Remove a subscription by endpoint URL. Returns True if it was found."""
        result = await self.db.execute(
            select(PushSubscription).where(
                PushSubscription.endpoint == endpoint,
                PushSubscription.user_id == user_id,
            )
        )
        sub = result.scalar_one_or_none()
        if sub:
            await self.db.delete(sub)
            await self.db.commit()
            return True
        return False

    async def send_to_all_subscribers(
        self,
        title: str,
        body: str,
        tag: str = "softarr",
        url: str = "/",
        require_interaction: bool = False,
    ) -> dict:
        """Send a push notification to all registered subscribers.

        Returns a summary dict with ``sent`` and ``failed`` counts.
        Expired/invalid subscriptions are removed automatically (410 Gone).
        """
        if not self.is_enabled():
            return {"sent": 0, "failed": 0, "skipped": "push_disabled"}

        public_key = self.ini.get("push_vapid_public_key") or ""
        private_key = self.ini.get("push_vapid_private_key") or ""
        claims_sub = self.ini.get("push_vapid_claims_sub") or "mailto:admin@example.com"

        if not public_key or not private_key:
            logger.warning("Push notification skipped: VAPID keys not configured")
            return {"sent": 0, "failed": 0, "skipped": "vapid_keys_missing"}

        result = await self.db.execute(select(PushSubscription))
        subscriptions = list(result.scalars().all())
        if not subscriptions:
            return {"sent": 0, "failed": 0}

        payload = json.dumps(
            {
                "title": title,
                "body": body,
                "tag": tag,
                "url": url,
                "requireInteraction": require_interaction,
            }
        ).encode("utf-8")

        sent = 0
        failed = 0
        to_remove: List[PushSubscription] = []

        for sub in subscriptions:
            try:
                await self._send_one(
                    sub=sub,
                    payload=payload,
                    public_key=public_key,
                    private_key=private_key,
                    claims_sub=claims_sub,
                )
                sent += 1
            except Exception as exc:
                err_str = str(exc)
                if "410" in err_str or "404" in err_str:
                    # Subscription expired or unregistered -- remove it
                    to_remove.append(sub)
                else:
                    logger.warning(
                        "Push delivery failed for endpoint %s: %s",
                        sub.endpoint[:40],
                        exc,
                    )
                failed += 1

        for sub in to_remove:
            await self.db.delete(sub)
        if to_remove:
            await self.db.commit()

        return {"sent": sent, "failed": failed}

    @staticmethod
    async def _send_one(
        sub: PushSubscription,
        payload: bytes,
        public_key: str,
        private_key: str,
        claims_sub: str,
    ) -> None:
        """Send a single push notification using pywebpush."""
        import asyncio

        from pywebpush import webpush

        subscription_info = {
            "endpoint": sub.endpoint,
            "keys": {
                "p256dh": sub.p256dh,
                "auth": sub.auth,
            },
        }
        vapid_claims = {"sub": claims_sub}

        # pywebpush is synchronous; run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims=vapid_claims,
                content_encoding="aes128gcm",
            ),
        )
