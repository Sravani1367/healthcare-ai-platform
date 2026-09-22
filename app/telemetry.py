from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent, CapabilityExecution, OperationalEvent
from .security import RequestContext


SENSITIVE_KEYS = {
    "password", "password_hash", "token", "authorization", "api_key",
    "answer", "answers", "transcript", "raw_audio", "secret",
}


def safe_metadata(values: dict[str, Any] | None) -> dict[str, Any]:
    if not values:
        return {}
    result: dict[str, Any] = {}
    for key, value in values.items():
        lowered = key.lower()
        if any(sensitive in lowered for sensitive in SENSITIVE_KEYS):
            result[key] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
        else:
            result[key] = str(value)[:300]
    return result


def record_audit(
    db: Session,
    ctx: RequestContext,
    action: str,
    outcome: str,
    *,
    hospital_id: int | None = None,
    resource_type: str | None = None,
    resource_id: int | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(AuditEvent(
        correlation_id=ctx.correlation_id,
        hospital_id=hospital_id,
        actor_id=ctx.user_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        outcome=outcome,
        metadata_json=json.dumps(safe_metadata(metadata)),
    ))


def record_event(
    db: Session,
    correlation_id: str,
    event_type: str,
    outcome: str,
    *,
    hospital_id: int | None = None,
    resource_type: str | None = None,
    resource_id: int | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(OperationalEvent(
        correlation_id=correlation_id,
        hospital_id=hospital_id,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        outcome=outcome,
        metadata_json=json.dumps(safe_metadata(metadata)),
    ))


def record_capability(
    db: Session,
    ctx: RequestContext,
    name: str,
    input_data: dict[str, Any],
    outcome: str,
    *,
    hospital_id: int | None = None,
    error_code: str | None = None,
) -> None:
    canonical = json.dumps(input_data, sort_keys=True, default=str, separators=(",", ":"))
    db.add(CapabilityExecution(
        capability_name=name,
        actor_id=ctx.user_id,
        hospital_id=hospital_id,
        correlation_id=ctx.correlation_id,
        input_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        outcome=outcome,
        error_code=error_code,
    ))

