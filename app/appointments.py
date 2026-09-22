from __future__ import annotations

import hmac
import json
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .connectors import MockEHRConnector
from .idempotency import begin_idempotent, finish_idempotent
from .models import (
    Appointment,
    AppointmentHistory,
    ExternalIdentifierMapping,
    HealthcareSystemConnection,
    IntegrationOperation,
    IntegrationVerification,
    Patient,
    ReconciliationRecord,
    SlotReservation,
    utcnow,
)
from .scheduling import as_utc_iso, validate_slot
from .security import RequestContext
from .telemetry import record_audit, record_capability, record_event
from .workflows import cancel_pending_workflows, enqueue_workflows


def _connection(db: Session, hospital_id: int) -> HealthcareSystemConnection:
    item = db.scalar(select(HealthcareSystemConnection).where(
        HealthcareSystemConnection.hospital_id == hospital_id,
        HealthcareSystemConnection.status == "ACTIVE",
    ))
    if item is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "INTEGRATION_NOT_CONFIGURED", "message": "Hospital integration is not active"},
        )
    return item


def _connector(connection: HealthcareSystemConnection) -> MockEHRConnector:
    if connection.connector_type != "MOCK_EHR":
        raise HTTPException(status_code=422, detail={
            "code": "UNSUPPORTED_CONNECTOR", "message": "Connector is not supported"
        })
    return MockEHRConnector(connection.base_url)


def _history(
    db: Session,
    appointment: Appointment,
    to_status: str,
    actor_id: int | None,
    reason: str | None = None,
) -> None:
    old = appointment.status
    appointment.status = to_status
    appointment.version += 1
    db.add(AppointmentHistory(
        appointment_id=appointment.id,
        from_status=old,
        to_status=to_status,
        actor_id=actor_id,
        reason=reason,
    ))


def _appointment_response(appointment: Appointment) -> dict:
    return {
        "id": appointment.id,
        "hospital_id": appointment.hospital_id,
        "patient_id": appointment.patient_id,
        "doctor_id": appointment.doctor_id,
        "appointment_type_id": appointment.appointment_type_id,
        "starts_at": as_utc_iso(appointment.starts_at),
        "ends_at": as_utc_iso(appointment.ends_at),
        "status": appointment.status,
        "external_id": appointment.external_id,
        "version": appointment.version,
    }


def _mapping(
    db: Session,
    connection_id: int,
    entity_type: str,
    internal_id: int,
    external_id: str,
) -> None:
    existing = db.scalar(select(ExternalIdentifierMapping).where(
        ExternalIdentifierMapping.connection_id == connection_id,
        ExternalIdentifierMapping.entity_type == entity_type,
        ExternalIdentifierMapping.internal_id == internal_id,
    ))
    if existing is None:
        db.add(ExternalIdentifierMapping(
            connection_id=connection_id,
            entity_type=entity_type,
            internal_id=internal_id,
            external_id=external_id,
        ))


def create_appointment(
    db: Session,
    ctx: RequestContext,
    *,
    doctor_id: int,
    appointment_type_id: int,
    starts_at: datetime,
    idempotency_key: str,
    slot_quote_id: str,
    confirmation_token: str,
) -> dict:
    if ctx.role != "PATIENT" or ctx.patient_id is None:
        raise HTTPException(status_code=403, detail={
            "code": "FORBIDDEN", "message": "Only a registered patient can self-book"
        })
    if not hmac.compare_digest(confirmation_token, slot_quote_id):
        # The confirmation token must be the exact quote the patient was shown and
        # explicitly confirmed — this is what proves a distinct confirm step happened,
        # not just a single create call (PRD §9 step 3).
        raise HTTPException(status_code=409, detail={
            "code": "CONFIRMATION_TOKEN_MISMATCH",
            "message": "The confirmation token does not match the quoted slot.",
        })
    payload = {
        "doctor_id": doctor_id,
        "appointment_type_id": appointment_type_id,
        "starts_at": starts_at.isoformat(),
    }
    idem, replay = begin_idempotent(
        db, actor_id=ctx.user_id, operation="CREATE_APPOINTMENT",
        key=idempotency_key, payload=payload,
    )
    if replay is not None:
        return replay
    hospital, _doctor, _kind, calendar, slot_start, slot_end = validate_slot(
        db,
        doctor_id=doctor_id,
        appointment_type_id=appointment_type_id,
        starts_at=starts_at,
        slot_quote_id=slot_quote_id,
    )
    patient = db.get(Patient, ctx.patient_id)
    connection = _connection(db, hospital.id)
    connector = _connector(connection)
    lookups = {
        "PATIENT": (patient.id, connector.lookup_patient(patient.id)),
        "PROVIDER": (doctor_id, connector.lookup_provider(doctor_id)),
        "FACILITY": (hospital.id, connector.lookup_facility(hospital.id)),
        "CALENDAR": (calendar.id, connector.lookup_calendar(calendar.id)),
    }
    failed = [kind for kind, (_, result) in lookups.items() if not result.succeeded or not result.data.get("external_id")]
    if failed:
        raise HTTPException(status_code=409, detail={
            "code": "EXTERNAL_MAPPING_FAILED",
            "message": f"Required external mappings unavailable: {', '.join(failed)}",
        })
    appointment = Appointment(
        hospital_id=hospital.id,
        patient_id=patient.id,
        doctor_id=doctor_id,
        calendar_id=calendar.id,
        appointment_type_id=appointment_type_id,
        starts_at=slot_start,
        ends_at=slot_end,
        status="REQUESTED",
        idempotency_key=idempotency_key,
    )
    db.add(appointment)
    db.flush()
    db.add(SlotReservation(
        doctor_id=doctor_id,
        appointment_id=appointment.id,
        starts_at=slot_start,
        ends_at=slot_end,
    ))
    db.add(AppointmentHistory(
        appointment_id=appointment.id,
        from_status=None,
        to_status="REQUESTED",
        actor_id=ctx.user_id,
    ))
    operation = IntegrationOperation(
        hospital_id=hospital.id,
        connection_id=connection.id,
        appointment_id=appointment.id,
        operation_type="CREATE_APPOINTMENT",
        status="PENDING",
        idempotency_key=f"ehr:create:{idempotency_key}",
        correlation_id=ctx.correlation_id,
        attempt_count=1,
    )
    db.add(operation)
    _history(db, appointment, "EXTERNAL_PENDING", ctx.user_id)
    record_capability(db, ctx, "create_appointment", payload, "STARTED", hospital_id=hospital.id)
    record_event(
        db, ctx.correlation_id, "AppointmentRequested", "SUCCEEDED",
        hospital_id=hospital.id, resource_type="appointment", resource_id=appointment.id,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "SLOT_NO_LONGER_AVAILABLE",
            "message": "Another request booked this slot first",
        }) from exc

    result = connector.create_appointment(
        patient_id=patient.id,
        doctor_id=doctor_id,
        date=slot_start.date().isoformat(),
        time=slot_start.time().replace(microsecond=0).isoformat(timespec="minutes"),
        idempotency_key=operation.idempotency_key,
    )
    while result.classification == "RETRYABLE" and operation.attempt_count < 3:
        operation.attempt_count += 1
        result = connector.create_appointment(
            patient_id=patient.id,
            doctor_id=doctor_id,
            date=slot_start.date().isoformat(),
            time=slot_start.time().replace(microsecond=0).isoformat(timespec="minutes"),
            idempotency_key=operation.idempotency_key,
        )
    operation.status = result.classification
    operation.last_error_message = result.message
    external_id = result.data.get("external_id")

    if result.classification == "UNKNOWN_OUTCOME":
        searched = connector.search_appointment(
            patient_id=patient.id,
            doctor_id=doctor_id,
            date=slot_start.date().isoformat(),
            time=slot_start.time().replace(microsecond=0).isoformat(timespec="minutes"),
        )
        if searched.succeeded:
            external_id = searched.data.get("external_id")
            result = searched

    if result.succeeded and external_id:
        operation.external_id = external_id
        expected = {
            "patient_id": patient.id,
            "doctor_id": doctor_id,
            "date": slot_start.date().isoformat(),
            "time": slot_start.time().replace(microsecond=0).isoformat(timespec="minutes"),
            "status": "CONFIRMED",
        }
        verified = connector.verify_appointment(external_id=external_id, expected=expected)
        db.add(IntegrationVerification(
            operation_id=operation.id,
            status=verified.classification,
            evidence_json=json.dumps(verified.data, default=str),
            verified_at=utcnow(),
        ))
        if verified.succeeded:
            operation.status = "VERIFIED"
            appointment.external_id = external_id
            _mapping(db, connection.id, "APPOINTMENT", appointment.id, external_id)
            for entity_type, (internal_id, lookup) in lookups.items():
                _mapping(db, connection.id, entity_type, internal_id, lookup.data["external_id"])
            _history(db, appointment, "CONFIRMED", ctx.user_id)
            enqueue_workflows(
                db, trigger_event="APPOINTMENT_CONFIRMED",
                appointment=appointment, correlation_id=ctx.correlation_id,
            )
            response = _appointment_response(appointment)
            finish_idempotent(idem, response, 201)
            record_capability(db, ctx, "create_appointment", payload, "SUCCEEDED", hospital_id=hospital.id)
            record_audit(
                db, ctx, "APPOINTMENT_CREATE", "SUCCEEDED",
                hospital_id=hospital.id, resource_type="appointment", resource_id=appointment.id,
            )
            record_event(
                db, ctx.correlation_id, "ExternalVerificationCompleted", "SUCCEEDED",
                hospital_id=hospital.id, resource_type="appointment", resource_id=appointment.id,
            )
            db.commit()
            return response

    appointment.status = "RECONCILIATION_REQUIRED"
    operation.status = "RECONCILIATION_REQUIRED"
    db.add(ReconciliationRecord(
        hospital_id=hospital.id,
        operation_id=operation.id,
        appointment_id=appointment.id,
        reason=result.message or f"External result was {result.classification}",
    ))
    response = _appointment_response(appointment)
    finish_idempotent(idem, response, 202)
    record_capability(
        db, ctx, "create_appointment", payload, "PENDING",
        hospital_id=hospital.id, error_code=result.classification,
    )
    db.commit()
    return response


def cancel_appointment(
    db: Session,
    ctx: RequestContext,
    appointment: Appointment,
    *,
    idempotency_key: str,
    expected_version: int,
    reason: str | None,
) -> dict:
    if appointment.version != expected_version:
        raise HTTPException(status_code=409, detail={
            "code": "VERSION_CONFLICT", "message": "Appointment has changed"
        })
    if ctx.role == "PATIENT" and appointment.patient_id != ctx.patient_id:
        raise HTTPException(status_code=404, detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found"})
    ctx.require_hospital(appointment.hospital_id) if ctx.role != "PATIENT" else None
    idem, replay = begin_idempotent(
        db, actor_id=ctx.user_id, operation="CANCEL_APPOINTMENT", key=idempotency_key,
        payload={"appointment_id": appointment.id, "expected_version": expected_version, "reason": reason},
    )
    if replay is not None:
        return replay
    if appointment.status not in {"CONFIRMED", "RESCHEDULED"} or not appointment.external_id:
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATE", "message": "Appointment cannot be cancelled"})
    connection = _connection(db, appointment.hospital_id)
    result = _connector(connection).cancel_appointment(
        external_id=appointment.external_id,
        idempotency_key=f"ehr:cancel:{idempotency_key}",
    )
    verified = _connector(connection).get_appointment(appointment.external_id)
    external = verified.data.get("appointment") or {}
    if result.succeeded and verified.succeeded and external.get("status") == "CANCELLED":
        _history(db, appointment, "CANCELLED", ctx.user_id, reason)
        db.execute(delete(SlotReservation).where(
            SlotReservation.appointment_id == appointment.id
        ))
        cancel_pending_workflows(db, appointment=appointment, correlation_id=ctx.correlation_id)
        response = _appointment_response(appointment)
        finish_idempotent(idem, response)
        enqueue_workflows(
            db, trigger_event="APPOINTMENT_CANCELLED", appointment=appointment,
            correlation_id=ctx.correlation_id,
        )
        record_audit(db, ctx, "APPOINTMENT_CANCEL", "SUCCEEDED", hospital_id=appointment.hospital_id, resource_type="appointment", resource_id=appointment.id)
        db.commit()
        return response
    raise HTTPException(status_code=202, detail={
        "code": "RECONCILIATION_REQUIRED", "message": "Cancellation outcome requires reconciliation"
    })


def reschedule_appointment(
    db: Session,
    ctx: RequestContext,
    appointment: Appointment,
    *,
    starts_at: datetime,
    idempotency_key: str,
    expected_version: int,
    slot_quote_id: str,
    confirmation_token: str,
) -> dict:
    if appointment.version != expected_version:
        raise HTTPException(status_code=409, detail={"code": "VERSION_CONFLICT", "message": "Appointment has changed"})
    if ctx.role == "PATIENT" and appointment.patient_id != ctx.patient_id:
        raise HTTPException(status_code=404, detail={"code": "RESOURCE_NOT_FOUND", "message": "Resource not found"})
    if appointment.status not in {"CONFIRMED", "RESCHEDULED"} or not appointment.external_id:
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATE", "message": "Appointment cannot be rescheduled"})
    if not hmac.compare_digest(confirmation_token, slot_quote_id):
        raise HTTPException(status_code=409, detail={
            "code": "CONFIRMATION_TOKEN_MISMATCH",
            "message": "The confirmation token does not match the quoted slot.",
        })
    idem, replay = begin_idempotent(
        db, actor_id=ctx.user_id, operation="RESCHEDULE_APPOINTMENT", key=idempotency_key,
        payload={"appointment_id": appointment.id, "starts_at": starts_at.isoformat(), "expected_version": expected_version},
    )
    if replay is not None:
        return replay
    _, _, _, calendar, new_start, new_end = validate_slot(
        db, doctor_id=appointment.doctor_id,
        appointment_type_id=appointment.appointment_type_id,
        starts_at=starts_at,
        slot_quote_id=slot_quote_id,
    )
    old_start, old_end = appointment.starts_at, appointment.ends_at
    db.execute(delete(SlotReservation).where(SlotReservation.appointment_id == appointment.id))
    reservation = SlotReservation(
        doctor_id=appointment.doctor_id, appointment_id=appointment.id,
        starts_at=new_start, ends_at=new_end,
    )
    db.add(reservation)
    db.flush()
    connection = _connection(db, appointment.hospital_id)
    connector = _connector(connection)
    result = connector.reschedule_appointment(
        external_id=appointment.external_id,
        date=new_start.date().isoformat(),
        time=new_start.time().replace(microsecond=0).isoformat(timespec="minutes"),
        idempotency_key=f"ehr:reschedule:{idempotency_key}",
    )
    verified = connector.verify_appointment(
        external_id=appointment.external_id,
        expected={
            "patient_id": appointment.patient_id,
            "doctor_id": appointment.doctor_id,
            "date": new_start.date().isoformat(),
            "time": new_start.time().replace(microsecond=0).isoformat(timespec="minutes"),
            "status": "CONFIRMED",
        },
    )
    if result.succeeded and verified.succeeded:
        appointment.starts_at = new_start
        appointment.ends_at = new_end
        appointment.calendar_id = calendar.id
        _history(db, appointment, "RESCHEDULED", ctx.user_id)
        response = _appointment_response(appointment)
        finish_idempotent(idem, response)
        enqueue_workflows(db, trigger_event="APPOINTMENT_RESCHEDULED", appointment=appointment, correlation_id=ctx.correlation_id)
        record_audit(db, ctx, "APPOINTMENT_RESCHEDULE", "SUCCEEDED", hospital_id=appointment.hospital_id, resource_type="appointment", resource_id=appointment.id)
        db.commit()
        return response
    db.delete(reservation)
    db.add(SlotReservation(
        doctor_id=appointment.doctor_id, appointment_id=appointment.id,
        starts_at=old_start, ends_at=old_end,
    ))
    db.commit()
    raise HTTPException(status_code=202, detail={
        "code": "RECONCILIATION_REQUIRED", "message": "Reschedule outcome requires reconciliation"
    })
