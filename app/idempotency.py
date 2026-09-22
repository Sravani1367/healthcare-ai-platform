from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import IdempotencyRecord


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def begin_idempotent(
    db: Session,
    *,
    actor_id: int,
    operation: str,
    key: str,
    payload: dict[str, Any],
) -> tuple[IdempotencyRecord, dict[str, Any] | None]:
    digest = request_hash(payload)
    existing = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.actor_id == actor_id,
        IdempotencyRecord.operation == operation,
        IdempotencyRecord.idempotency_key == key,
    ))
    if existing:
        if existing.request_hash != digest:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "IDEMPOTENCY_KEY_REUSED",
                    "message": "The idempotency key was used for a different request",
                },
            )
        if existing.response_json:
            return existing, json.loads(existing.response_json)
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail={
                "code": "OPERATION_IN_PROGRESS",
                "message": "The original request is still being processed",
            },
        )
    record = IdempotencyRecord(
        actor_id=actor_id,
        operation=operation,
        idempotency_key=key,
        request_hash=digest,
    )
    db.add(record)
    db.flush()
    return record, None


def finish_idempotent(
    record: IdempotencyRecord, response: dict[str, Any], status_code: int = 200
) -> None:
    record.response_json = json.dumps(response, default=str)
    record.status_code = status_code
    record.state = "COMPLETED"

