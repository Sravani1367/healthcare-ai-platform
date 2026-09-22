from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Appointment,
    AppointmentType,
    BlockedPeriod,
    Calendar,
    Doctor,
    Hospital,
    SlotReservation,
    WorkingHours,
)
from .security import sign_payload, verify_signed_payload


SLOT_QUOTE_TTL_SECONDS = 300


def _create_slot_quote(*, doctor_id: int, appointment_type_id: int, calendar_id: int, starts_at_iso: str) -> tuple[str, str]:
    """Returns (quote_id, expires_at_iso). A quote is a signed, opaque, short-lived
    claim that a specific slot looked bookable at the moment it was quoted — never a
    guarantee, callers must still revalidate at commit time (PRD §9 step 2)."""
    now = int(datetime.now(timezone.utc).timestamp())
    expires_at = now + SLOT_QUOTE_TTL_SECONDS
    quote_id = sign_payload({
        "doctor_id": doctor_id,
        "appointment_type_id": appointment_type_id,
        "calendar_id": calendar_id,
        "starts_at": starts_at_iso,
        "exp": expires_at,
    })
    return quote_id, datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def verify_slot_quote(quote_id: str, *, doctor_id: int, appointment_type_id: int, starts_at_iso: str) -> None:
    """Raises HTTPException(409) if the quote is missing, tampered, expired, or does
    not match the slot actually being booked."""
    try:
        payload = verify_signed_payload(quote_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={
            "code": "STALE_QUOTE", "message": "This quote is invalid or has expired. Please check availability again.",
        }) from exc
    if (
        payload.get("doctor_id") != doctor_id
        or payload.get("appointment_type_id") != appointment_type_id
        or payload.get("starts_at") != starts_at_iso
    ):
        raise HTTPException(status_code=409, detail={
            "code": "STALE_QUOTE", "message": "This quote does not match the requested slot.",
        })


OCCUPYING_STATES = {
    "REQUESTED", "EXTERNAL_PENDING", "VERIFIED", "CONFIRMED",
    "RESCHEDULED", "SYNCHRONIZATION_PENDING", "RECONCILIATION_REQUIRED",
}


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "Unknown timezone"},
        ) from exc


def as_utc_naive(value: datetime, assumed_timezone: str = "UTC") -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=_tz(assumed_timezone))
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def as_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _calendar_for(
    db: Session, doctor_id: int, appointment_type_id: int
) -> tuple[Hospital, Doctor, AppointmentType, Calendar]:
    doctor = db.get(Doctor, doctor_id)
    appointment_type = db.get(AppointmentType, appointment_type_id)
    if doctor is None or appointment_type is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "RESOURCE_NOT_FOUND", "message": "Doctor or appointment type not found"},
        )
    if doctor.hospital_id != appointment_type.hospital_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "APPOINTMENT_TYPE_MISMATCH", "message": "Appointment type is not offered by this doctor"},
        )
    hospital = db.get(Hospital, doctor.hospital_id)
    if hospital is None or hospital.status != "APPROVED":
        raise HTTPException(
            status_code=409,
            detail={"code": "HOSPITAL_NOT_BOOKABLE", "message": "Hospital is not approved for booking"},
        )
    if doctor.status != "ACTIVE" or appointment_type.status != "ACTIVE":
        raise HTTPException(
            status_code=409,
            detail={"code": "DOCTOR_NOT_BOOKABLE", "message": "Doctor or appointment type is inactive"},
        )
    calendar = db.scalar(select(Calendar).where(
        Calendar.doctor_id == doctor_id,
        Calendar.appointment_type_id == appointment_type_id,
        Calendar.status == "ACTIVE",
    ))
    if calendar is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "CALENDAR_NOT_ACTIVE", "message": "No active calendar is configured"},
        )
    return hospital, doctor, appointment_type, calendar


def list_available_slots(
    db: Session,
    *,
    doctor_id: int,
    appointment_type_id: int,
    date_from: date,
    date_to: date,
) -> list[dict]:
    hospital, _doctor, appointment_type, calendar = _calendar_for(
        db, doctor_id, appointment_type_id
    )
    local_tz = _tz(calendar.timezone)
    hours = db.scalars(select(WorkingHours).where(
        WorkingHours.calendar_id == calendar.id
    )).all()
    by_weekday: dict[int, list[WorkingHours]] = {}
    for window in hours:
        by_weekday.setdefault(window.weekday, []).append(window)

    range_start = datetime.combine(date_from, time.min, local_tz)
    range_end = datetime.combine(date_to + timedelta(days=1), time.min, local_tz)
    utc_start = as_utc_naive(range_start)
    utc_end = as_utc_naive(range_end)
    blocks = db.scalars(select(BlockedPeriod).where(
        BlockedPeriod.calendar_id == calendar.id,
        BlockedPeriod.starts_at < utc_end,
        BlockedPeriod.ends_at > utc_start,
    )).all()
    reservations = db.scalars(select(SlotReservation).where(
        SlotReservation.doctor_id == doctor_id,
        SlotReservation.starts_at < utc_end,
        SlotReservation.ends_at > utc_start,
    )).all()

    slots: list[dict] = []
    current_day = date_from
    duration = timedelta(minutes=appointment_type.duration_minutes)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    while current_day <= date_to:
        for window in sorted(by_weekday.get(current_day.weekday(), []), key=lambda x: x.start_time):
            local_cursor = datetime.combine(current_day, window.start_time, local_tz)
            local_end = datetime.combine(current_day, window.end_time, local_tz)
            while local_cursor + duration <= local_end:
                candidate_start = as_utc_naive(local_cursor)
                candidate_end = as_utc_naive(local_cursor + duration)
                blocked = any(
                    block.starts_at < candidate_end and block.ends_at > candidate_start
                    for block in blocks
                )
                reserved = any(
                    item.starts_at < candidate_end and item.ends_at > candidate_start
                    for item in reservations
                )
                if candidate_start > now and not blocked and not reserved:
                    starts_at_iso = as_utc_iso(candidate_start)
                    quote_id, quote_expires_at = _create_slot_quote(
                        doctor_id=doctor_id, appointment_type_id=appointment_type_id,
                        calendar_id=calendar.id, starts_at_iso=starts_at_iso,
                    )
                    slots.append({
                        "hospital_id": hospital.id,
                        "doctor_id": doctor_id,
                        "calendar_id": calendar.id,
                        "appointment_type_id": appointment_type_id,
                        "starts_at": starts_at_iso,
                        "ends_at": as_utc_iso(candidate_end),
                        "timezone": calendar.timezone,
                        "slot_quote_id": quote_id,
                        "quote_expires_at": quote_expires_at,
                    })
                local_cursor += duration
        current_day += timedelta(days=1)
    return slots


def validate_slot(
    db: Session,
    *,
    doctor_id: int,
    appointment_type_id: int,
    starts_at: datetime,
    slot_quote_id: str,
) -> tuple[Hospital, Doctor, AppointmentType, Calendar, datetime, datetime]:
    hospital, doctor, appointment_type, calendar = _calendar_for(
        db, doctor_id, appointment_type_id
    )
    requested_utc = as_utc_naive(starts_at, calendar.timezone)
    expected = as_utc_iso(requested_utc)
    verify_slot_quote(
        slot_quote_id, doctor_id=doctor_id, appointment_type_id=appointment_type_id,
        starts_at_iso=expected,
    )
    local_date = requested_utc.replace(tzinfo=timezone.utc).astimezone(
        _tz(calendar.timezone)
    ).date()
    candidates = list_available_slots(
        db,
        doctor_id=doctor_id,
        appointment_type_id=appointment_type_id,
        date_from=local_date,
        date_to=local_date,
    )
    if not any(slot["starts_at"] == expected for slot in candidates):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SLOT_NO_LONGER_AVAILABLE",
                "message": "The selected slot is invalid or no longer available",
            },
        )
    ends_at = requested_utc + timedelta(minutes=appointment_type.duration_minutes)
    return hospital, doctor, appointment_type, calendar, requested_utc, ends_at

