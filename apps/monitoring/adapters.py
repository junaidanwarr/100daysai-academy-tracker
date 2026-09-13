"""
Notification delivery adapters (specification section 14).

One interface, one adapter per channel. An unconfigured adapter reports SKIPPED
with a readable reason rather than raising — a missing Twilio key must not stop
the in-app notification, and the reason is stored on the notification row so
the UI can explain why an SMS never arrived.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings

from apps.core.enums import NotificationChannel, NotificationDeliveryStatus

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    status: str
    detail: str | None = None
    provider_message_id: str | None = None


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 15) -> tuple[int, str]:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


class InAppAdapter:
    """In-app delivery is the notification row itself; nothing external to call."""

    channel = NotificationChannel.IN_APP
    label = "In-app"

    def is_configured(self) -> bool:
        return True

    def send(self, to: str, title: str, body: str, link_url: str | None = None) -> DeliveryResult:
        return DeliveryResult(NotificationDeliveryStatus.SENT, "Stored for in-app display.")


class EmailAdapter:
    channel = NotificationChannel.EMAIL
    label = "Email"

    def is_configured(self) -> bool:
        return bool(settings.RESEND_API_KEY)

    def send(self, to: str, title: str, body: str, link_url: str | None = None) -> DeliveryResult:
        if not self.is_configured():
            # Development default: log rather than fail, so the whole alert
            # pipeline is exercisable before any email provider exists.
            logger.info("[email:not-configured] to=%s subject=%s", to, title)
            return DeliveryResult(
                NotificationDeliveryStatus.SKIPPED,
                "RESEND_API_KEY is not set; email logged to the console.",
            )

        text = f"{body}\n\n{link_url}" if link_url else body
        try:
            status, raw = _post_json(
                "https://api.resend.com/emails",
                {"from": settings.NOTIFY_FROM_EMAIL, "to": [to], "subject": title, "text": text},
                {"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            )
            if status >= 400:
                return DeliveryResult(NotificationDeliveryStatus.FAILED, f"Resend returned {status}: {raw[:200]}")
            return DeliveryResult(NotificationDeliveryStatus.SENT, provider_message_id=json.loads(raw or "{}").get("id"))
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(NotificationDeliveryStatus.FAILED, str(exc)[:300])


class SmsAdapter:
    channel = NotificationChannel.SMS
    label = "SMS"

    def is_configured(self) -> bool:
        return bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_FROM_NUMBER)

    def send(self, to: str, title: str, body: str, link_url: str | None = None) -> DeliveryResult:
        if not self.is_configured():
            return DeliveryResult(NotificationDeliveryStatus.SKIPPED, "Twilio credentials are not configured.")

        sid = settings.TWILIO_ACCOUNT_SID
        credentials = base64.b64encode(f"{sid}:{settings.TWILIO_AUTH_TOKEN}".encode()).decode()
        # SMS is a nudge, not the full record — keep it short and point back.
        payload = urllib.parse.urlencode(
            {"To": to, "From": settings.TWILIO_FROM_NUMBER, "Body": f"{title}: {body}"[:320]}
        ).encode()

        request = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data=payload,
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return DeliveryResult(
                    NotificationDeliveryStatus.SENT,
                    provider_message_id=json.loads(response.read().decode() or "{}").get("sid"),
                )
        except urllib.error.HTTPError as exc:
            return DeliveryResult(NotificationDeliveryStatus.FAILED, f"Twilio returned {exc.code}: {exc.read().decode()[:200]}")
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(NotificationDeliveryStatus.FAILED, str(exc)[:300])


class WhatsAppAdapter:
    channel = NotificationChannel.WHATSAPP
    label = "WhatsApp"

    def is_configured(self) -> bool:
        return bool(settings.WHATSAPP_PHONE_NUMBER_ID and settings.WHATSAPP_ACCESS_TOKEN)

    def send(self, to: str, title: str, body: str, link_url: str | None = None) -> DeliveryResult:
        if not self.is_configured():
            return DeliveryResult(NotificationDeliveryStatus.SKIPPED, "WhatsApp Cloud API credentials are not configured.")

        try:
            status, raw = _post_json(
                f"https://graph.facebook.com/v21.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages",
                {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": f"*{title}*\n{body}"}},
                {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},
            )
            if status >= 400:
                return DeliveryResult(NotificationDeliveryStatus.FAILED, f"WhatsApp API returned {status}: {raw[:200]}")
            messages = json.loads(raw or "{}").get("messages") or [{}]
            return DeliveryResult(NotificationDeliveryStatus.SENT, provider_message_id=messages[0].get("id"))
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(NotificationDeliveryStatus.FAILED, str(exc)[:300])


class PushAdapter:
    """Declared but intentionally unimplemented (the spec calls it optional)."""

    channel = NotificationChannel.PUSH
    label = "Push"

    def is_configured(self) -> bool:
        return False

    def send(self, to: str, title: str, body: str, link_url: str | None = None) -> DeliveryResult:
        return DeliveryResult(NotificationDeliveryStatus.SKIPPED, "Push notifications are not implemented yet.")


ADAPTERS = {
    NotificationChannel.IN_APP: InAppAdapter(),
    NotificationChannel.EMAIL: EmailAdapter(),
    NotificationChannel.SMS: SmsAdapter(),
    NotificationChannel.WHATSAPP: WhatsAppAdapter(),
    NotificationChannel.PUSH: PushAdapter(),
}


def configured_channels() -> list[str]:
    return [key for key, adapter in ADAPTERS.items() if adapter.is_configured()]
