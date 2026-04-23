"""Notification service.

Sends notifications to configured channels when key events occur:
  - new_release_discovered: a new release was found by search/scheduler
  - release_flagged: a release received a non-NONE flag status
  - download_queued: a release was successfully sent to SABnzbd
  - upgrade_available: a newer version was found for a monitored entry
  - download_complete: a release transitioned to DOWNLOADED state

Supported channels:
  - Email (SMTP via stdlib smtplib, sent in a thread executor)
  - Discord webhook (HTTP POST via httpx)
  - Generic HTTP webhook (HTTP POST via httpx)
  - Apprise-compatible webhook (HTTP POST, generic JSON -- works with ntfy,
    Gotify, Slack, and any Apprise-supported service via its webhook bridge)

Configuration (softarr.ini):
  [notifications]
  notifications_enabled = true
  notify_on_new_release = true
  notify_on_flagged = true
  notify_on_download = true
  notify_on_upgrade = true
  notify_on_download_complete = true
  email_enabled = false
  email_smtp_host = localhost
  email_smtp_port = 587
  email_smtp_user =
  email_smtp_password =
  email_from = softarr@example.com
  email_to = admin@example.com
  discord_webhook_enabled = false
  discord_webhook_url =
  http_webhook_enabled = false
  http_webhook_url =
  apprise_webhook_enabled = false
  apprise_webhook_url =
"""

import asyncio
import logging
import smtplib
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("softarr.notifications")

_executor = ThreadPoolExecutor(max_workers=2)


class NotificationService:
    def __init__(self, ini, db: Optional[AsyncSession] = None) -> None:
        self.ini = ini
        self.db = db

    def _get(self, key: str, default: str = "") -> str:
        return self.ini.get(key) or default

    def _enabled(self, key: str) -> bool:
        return self._get(key).lower() == "true"

    def _channel_allows_event(self, channel_key: str, event: str) -> bool:
        """Return True if the event passes the per-channel filter.

        The filter value is either "all" (default -- allow everything) or a
        comma-separated list of event names. An empty value is treated as "all".
        """
        filter_str = self._get(channel_key, "all").strip()
        if not filter_str or filter_str.lower() == "all":
            return True
        allowed = {e.strip() for e in filter_str.split(",") if e.strip()}
        return event in allowed

    async def _record_history(
        self,
        event: str,
        channel: str,
        success: bool,
        error_message: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        """Persist a notification history entry when a db session is available."""
        if self.db is None:
            return
        try:
            from softarr.models.notification_history import NotificationHistory

            entry = NotificationHistory(
                event=event,
                channel=channel,
                success=success,
                error_message=error_message,
                payload=payload or {},
            )
            self.db.add(entry)
            await self.db.commit()
        except Exception as exc:
            logger.debug("Could not record notification history: %s", exc)

    async def notify(self, event: str, payload: dict) -> None:
        """Dispatch a notification for the given event to all enabled channels.

        This is fire-and-forget -- errors are logged but do not propagate.
        """
        if not self._enabled("notifications_enabled"):
            return

        event_map = {
            "new_release_discovered": "notify_on_new_release",
            "release_flagged": "notify_on_flagged",
            "download_queued": "notify_on_download",
            "upgrade_available": "notify_on_upgrade",
            "download_complete": "notify_on_download_complete",
        }
        if event in event_map and not self._enabled(event_map[event]):
            return

        message = self._format_message(event, payload)

        tasks = []
        if self._enabled("email_enabled") and self._channel_allows_event(
            "email_events", event
        ):
            tasks.append(self._send_email_with_history(event, message, payload))
        if self._enabled("discord_webhook_enabled") and self._channel_allows_event(
            "discord_events", event
        ):
            tasks.append(self._send_discord_with_history(event, message, payload))
        if self._enabled("http_webhook_enabled") and self._channel_allows_event(
            "http_webhook_events", event
        ):
            tasks.append(self._send_http_with_history(event, payload))
        if self._enabled("apprise_webhook_enabled") and self._channel_allows_event(
            "apprise_events", event
        ):
            tasks.append(self._send_apprise_with_history(event, message, payload))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_channel(self, channel: str) -> dict:
        """Send a test notification through the specified channel.

        Returns {"ok": bool, "error": str|None}.
        """
        test_payload = {
            "name": "Softarr",
            "version": "test",
            "source_type": "test",
        }
        test_message = "This is a test notification from Softarr."
        error_msg = None
        ok = False

        try:
            if channel == "email":
                await self._send_email("[Softarr] Test notification", test_message)
                ok = True
            elif channel == "discord":
                await self._send_discord_webhook(
                    "test_notification", test_message, test_payload
                )
                ok = True
            elif channel == "http":
                await self._send_http_webhook("test_notification", test_payload)
                ok = True
            elif channel == "apprise":
                await self._send_apprise_webhook(
                    "test_notification", test_message, test_payload
                )
                ok = True
            else:
                error_msg = f"Unknown channel: {channel}"
        except Exception as exc:
            error_msg = str(exc)
            ok = False

        return {"ok": ok, "error": error_msg}

    @staticmethod
    async def get_history(db: AsyncSession, limit: int = 50) -> list:
        """Return recent notification history entries, newest first."""
        from sqlalchemy import select

        from softarr.models.notification_history import NotificationHistory

        result = await db.execute(
            select(NotificationHistory)
            .order_by(NotificationHistory.sent_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    def _format_message(self, event: str, payload: dict) -> str:
        """Build a human-readable message string for the event."""
        name = payload.get("software_name") or payload.get("name") or "unknown"
        version = payload.get("version") or ""
        source = payload.get("source_type") or ""
        if event == "new_release_discovered":
            return f"New release discovered: {name} {version} (source: {source})"
        if event == "release_flagged":
            reasons = ", ".join(payload.get("flag_reasons") or [])
            return f"Release flagged: {name} {version} -- {reasons or 'unknown reason'}"
        if event == "download_queued":
            return f"Download queued: {name} {version}"
        if event == "upgrade_available":
            current = payload.get("current_version") or "unknown"
            return f"Upgrade available for {name}: {current} -> {version}"
        if event == "download_complete":
            return f"Download completed: {name} {version}"
        return f"Event: {event} -- {payload}"

    # -- Channel senders with history recording --

    async def _send_email_with_history(
        self, event: str, message: str, payload: dict
    ) -> None:
        error_msg = None
        try:
            await self._send_email(f"[Softarr] {event}", message)
        except Exception as exc:
            error_msg = str(exc)
        await self._record_history(
            event, "email", error_msg is None, error_msg, {"name": payload.get("name")}
        )

    async def _send_discord_with_history(
        self, event: str, message: str, payload: dict
    ) -> None:
        error_msg = None
        try:
            await self._send_discord_webhook(event, message, payload)
        except Exception as exc:
            error_msg = str(exc)
        await self._record_history(
            event,
            "discord",
            error_msg is None,
            error_msg,
            {"name": payload.get("name")},
        )

    async def _send_http_with_history(self, event: str, payload: dict) -> None:
        error_msg = None
        try:
            await self._send_http_webhook(event, payload)
        except Exception as exc:
            error_msg = str(exc)
        await self._record_history(
            event, "http", error_msg is None, error_msg, {"name": payload.get("name")}
        )

    async def _send_apprise_with_history(
        self, event: str, message: str, payload: dict
    ) -> None:
        error_msg = None
        try:
            await self._send_apprise_webhook(event, message, payload)
        except Exception as exc:
            error_msg = str(exc)
        await self._record_history(
            event,
            "apprise",
            error_msg is None,
            error_msg,
            {"name": payload.get("name")},
        )

    async def _send_email(self, subject: str, body: str) -> None:
        """Send an email notification using smtplib (in a thread executor)."""
        host = self._get("email_smtp_host", "localhost")
        port = int(self._get("email_smtp_port", "587"))
        user = self._get("email_smtp_user")
        password = self._get("email_smtp_password")
        from_addr = self._get("email_from", "softarr@localhost")
        to_addr = self._get("email_to")

        if not to_addr:
            logger.debug("Email notification skipped: no recipient configured")
            return

        def _send() -> None:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"] = from_addr
            msg["To"] = to_addr
            try:
                with smtplib.SMTP(host, port, timeout=15) as smtp:
                    smtp.ehlo()
                    if smtp.has_extn("STARTTLS"):
                        smtp.starttls()
                        smtp.ehlo()
                    if user and password:
                        smtp.login(user, password)
                    smtp.sendmail(from_addr, [to_addr], msg.as_string())
                logger.info("Email notification sent to %s", to_addr)
            except Exception as exc:
                logger.warning("Email notification failed: %s", exc)
                raise

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, _send)

    async def _send_discord_webhook(
        self, event: str, message: str, payload: dict
    ) -> None:
        """POST a Discord-formatted message to the configured webhook URL."""
        url = self._get("discord_webhook_url")
        if not url:
            return

        colour_map = {
            "new_release_discovered": 0x5865F2,
            "release_flagged": 0xFFA500,
            "download_queued": 0x57F287,
        }
        colour = colour_map.get(event, 0x99AAB5)

        body = {
            "embeds": [
                {
                    "title": f"Softarr -- {event.replace('_', ' ').title()}",
                    "description": message,
                    "color": colour,
                }
            ]
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=body)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Discord webhook returned HTTP {resp.status_code}")

    async def _send_http_webhook(self, event: str, payload: dict) -> None:
        """POST a JSON payload to the configured generic HTTP webhook URL."""
        url = self._get("http_webhook_url")
        if not url:
            return

        body = {"event": event, "data": payload}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json=body,
                headers={
                    "User-Agent": "softarr/1.0",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code not in (200, 201, 202, 204):
            raise RuntimeError(f"HTTP webhook returned HTTP {resp.status_code}")

    async def _send_apprise_webhook(
        self, event: str, message: str, payload: dict
    ) -> None:
        """POST a notification to an Apprise-compatible webhook URL.

        Apprise's stateless API endpoint (and compatible bridges like ntfy,
        Gotify direct, and the Apprise microservice) accept a simple JSON
        body with 'title', 'body', and optional 'type' fields.

        URL format: https://apprise.example.com/notify/softarr
        """
        url = self._get("apprise_webhook_url")
        if not url:
            return

        type_map = {
            "new_release_discovered": "info",
            "release_flagged": "warning",
            "download_queued": "success",
            "upgrade_available": "info",
            "download_complete": "success",
        }

        body = {
            "title": f"Softarr -- {event.replace('_', ' ').title()}",
            "body": message,
            "type": type_map.get(event, "info"),
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json=body,
                headers={
                    "User-Agent": "softarr/1.0",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code not in (200, 201, 202, 204):
            raise RuntimeError(f"Apprise webhook returned HTTP {resp.status_code}")
