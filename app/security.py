from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Iterable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db


bearer = HTTPBearer(auto_error=False)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters")
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"scrypt${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_text, digest_text = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=_b64decode(salt_text), n=2**14, r=8, p=1,
            dklen=32,
        )
        return hmac.compare_digest(digest, _b64decode(digest_text))
    except (ValueError, TypeError):
        return False


def sign_payload(payload: dict, *, secret: str | None = None) -> str:
    body = _b64encode(json.dumps(payload, separators=(",", ":"), default=str).encode())
    signature = hmac.new(
        (secret or settings.token_secret).encode(), body.encode(), hashlib.sha256
    ).digest()
    return f"{body}.{_b64encode(signature)}"


def verify_signed_payload(token: str, *, secret: str | None = None) -> dict:
    """Raises ValueError on any tamper/format/expiry problem; callers decide how to
    surface that (401 for auth tokens, 409 for slot quotes, etc.)."""
    body, _, supplied = token.partition(".")
    if not supplied:
        raise ValueError("malformed token")
    expected = hmac.new(
        (secret or settings.token_secret).encode(), body.encode(), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, _b64decode(supplied)):
        raise ValueError("invalid signature")
    payload = json.loads(_b64decode(body))
    if "exp" in payload and int(payload["exp"]) < int(time.time()):
        raise ValueError("expired")
    return payload


def create_access_token(user_id: int, role: str) -> str:
    now = int(time.time())
    return sign_payload({
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + settings.token_ttl_seconds,
    })


def decode_access_token(token: str) -> dict:
    try:
        return verify_signed_payload(token)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_REQUIRED", "message": "Invalid or expired token"},
        ) from exc


@dataclass(frozen=True)
class RequestContext:
    user_id: int
    role: str
    hospital_ids: frozenset[int]
    patient_id: int | None
    doctor_id: int | None
    correlation_id: str

    def require_hospital(self, hospital_id: int) -> None:
        if self.role != "PLATFORM_ADMIN" and hospital_id not in self.hospital_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found"},
            )


def get_current_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> RequestContext:
    from .models import Doctor, HospitalMembership, Patient, User

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_REQUIRED", "message": "Authentication required"},
        )
    payload = decode_access_token(credentials.credentials)
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_REQUIRED", "message": "Authentication required"},
        )
    memberships = db.scalars(
        select(HospitalMembership).where(
            HospitalMembership.user_id == user.id,
            HospitalMembership.status == "ACTIVE",
        )
    ).all()
    patient = db.scalar(select(Patient).where(Patient.user_id == user.id))
    doctor = db.scalar(select(Doctor).where(Doctor.user_id == user.id))
    return RequestContext(
        user_id=user.id,
        role=user.role,
        hospital_ids=frozenset(m.hospital_id for m in memberships),
        patient_id=patient.id if patient else None,
        doctor_id=doctor.id if doctor else None,
        correlation_id=getattr(request.state, "correlation_id", "unknown"),
    )


def require_roles(*roles: str):
    allowed = frozenset(roles)

    def dependency(ctx: RequestContext = Depends(get_current_context)) -> RequestContext:
        if ctx.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": "Operation is not permitted"},
            )
        return ctx

    return dependency


def require_any_hospital(ctx: RequestContext, hospital_ids: Iterable[int]) -> None:
    if ctx.role == "PLATFORM_ADMIN":
        return
    if not ctx.hospital_ids.intersection(hospital_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found"},
        )

