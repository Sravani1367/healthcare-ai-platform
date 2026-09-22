from __future__ import annotations

import json

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Notification, utcnow
from .telemetry import record_event


def queue_email(
    db: Session,
    *,
    hospital_id: int,
    appointment_id: int | None,
    recipient_user_id: int | None,
    recipient: str,
    notification_type: str,
    template_data: dict,
    idempotency_key: str,
) -> Notification:
    existing = db.scalar(select(Notification).where(
        Notification.idempotency_key == idempotency_key
    ))
    if existing:
        return existing
    item = Notification(
        hospital_id=hospital_id,
        appointment_id=appointment_id,
        recipient_user_id=recipient_user_id,
        channel="EMAIL",
        notification_type=notification_type,
        recipient=recipient,
        template_data_json=json.dumps(template_data, default=str),
        idempotency_key=idempotency_key,
    )
    db.add(item)
    db.flush()
    return item


def _email_html(notification: Notification) -> str:
    data = json.loads(notification.template_data_json or "{}")
    title = data.get("title", notification.notification_type.replace("_", " ").title())
    lines = data.get("lines", [])
    body = "".join(f"<li>{str(line)}</li>" for line in lines)
    return (
        "<div style='font-family:Arial,sans-serif;max-width:640px'>"
        f"<h2>{title}</h2><ul>{body}</ul>"
        "<p>Healthcare AI Platform</p></div>"
    )


def deliver_email(db: Session, notification: Notification, correlation_id: str) -> Notification:
    if notification.status in {"SENT", "DELIVERED"}:
        return notification
    notification.attempts += 1
    if not settings.resend_api_key:
        notification.status = "SIMULATED"
        notification.provider_message_id = f"simulated-{notification.id}"
        notification.sent_at = utcnow()
        record_event(
            db, correlation_id, "NotificationSimulated", "SUCCEEDED",
            hospital_id=notification.hospital_id,
            resource_type="notification", resource_id=notification.id,
            metadata={"channel": "EMAIL", "type": notification.notification_type},
        )
        return notification
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": notification.idempotency_key,
            },
            json={
                "from": settings.resend_from_email,
                "to": [notification.recipient],
                "subject": json.loads(notification.template_data_json).get(
                    "subject", "Healthcare appointment update"
                ),
                "html": _email_html(notification),
            },
            timeout=8,
        )
        response.raise_for_status()
        notification.status = "SENT"
        notification.provider_message_id = response.json().get("id")
        notification.sent_at = utcnow()
        notification.last_error = None
    except (requests.RequestException, ValueError) as exc:
        notification.status = "RETRYING" if notification.attempts < 3 else "FAILED"
        notification.last_error = str(exc)[:500]
    record_event(
        db, correlation_id, "NotificationDelivery", notification.status,
        hospital_id=notification.hospital_id,
        resource_type="notification", resource_id=notification.id,
        metadata={"channel": "EMAIL", "attempts": notification.attempts},
    )
    return notification


def apply_resend_webhook(db: Session, payload: dict) -> Notification | None:
    data = payload.get("data") or {}
    provider_id = data.get("email_id") or data.get("id")
    if not provider_id:
        return None
    notification = db.scalar(select(Notification).where(
        Notification.provider_message_id == provider_id
    ))
    if notification is None:
        return None
    mapping = {
        "email.delivered": "DELIVERED",
        "email.bounced": "BOUNCED",
        "email.failed": "FAILED",
        "email.sent": "SENT",
    }
    notification.status = mapping.get(payload.get("type"), notification.status)
    return notification

