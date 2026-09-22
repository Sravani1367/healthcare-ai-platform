from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .agent_runtime import process_message
from .appointments import _connection, _connector, cancel_appointment, reschedule_appointment
from .capabilities import CapabilityRunner
from .config import settings
from .db import SessionLocal, get_db
from .models import (
    AIEvaluation,
    Appointment,
    AppointmentHistory,
    AppointmentType,
    AuditEvent,
    BlockedPeriod,
    Calendar,
    CapabilityExecution,
    Department,
    Doctor,
    HealthcareSystemConnection,
    Hospital,
    HospitalMembership,
    HumanEscalation,
    IntegrationOperation,
    IntegrationVerification,
    Notification,
    OperationalEvent,
    Patient,
    Platform,
    Questionnaire,
    QuestionnaireAssignment,
    QuestionnaireQuestion,
    QuestionnaireResponse,
    ReconciliationRecord,
    Specialty,
    User,
    UserPreference,
    Workflow,
    WorkflowExecution,
    WorkingHours,
    utcnow,
)
from .notifications import apply_resend_webhook
from .schemas import (
    AgentMessageRequest,
    AppointmentCancelRequest,
    AppointmentCreateRequest,
    AppointmentRescheduleRequest,
    AppointmentTypeCreateRequest,
    AvailabilityQuery,
    BlockedPeriodCreateRequest,
    CalendarCreateRequest,
    DepartmentCreateRequest,
    EvaluationRequest,
    HealthcareConnectionRequest,
    HospitalCreateRequest,
    HospitalReviewRequest,
    LoginRequest,
    PatientProfileUpdateRequest,
    PreferenceRequest,
    QuestionnaireAnswersRequest,
    QuestionnaireCreateRequest,
    QuestionnaireProposeRequest,
    ReconciliationResolveRequest,
    RegisterRequest,
    SpecialtyCreateRequest,
    TelephoneEventRequest,
    VoiceTurnRequest,
    WorkflowCreateRequest,
    WorkingHoursCreateRequest,
    DoctorCreateRequest,
    DoctorReviewRequest,
)
from .scheduling import as_utc_iso, list_available_slots
from .security import (
    RequestContext,
    create_access_token,
    decode_access_token,
    get_current_context,
    hash_password,
    require_roles,
    verify_password,
)
from .telemetry import record_audit, record_event
from .workflows import process_due_workflows


router = APIRouter(prefix="/api/v1")


def _json(value) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _entity(obj, *, exclude: set[str] | None = None) -> dict:
    excluded = exclude or set()
    return {
        column.name: getattr(obj, column.name)
        for column in obj.__table__.columns
        if column.name not in excluded
    }


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={
        "code": "RESOURCE_NOT_FOUND", "message": "Resource not found"
    })


def _require_owned_hospital(
    db: Session, ctx: RequestContext, hospital_id: int, *, approved: bool = False
) -> Hospital:
    hospital = db.get(Hospital, hospital_id)
    if hospital is None:
        raise _not_found()
    ctx.require_hospital(hospital_id)
    if approved and hospital.status != "APPROVED":
        raise HTTPException(status_code=409, detail={
            "code": "HOSPITAL_NOT_APPROVED", "message": "Hospital must be approved first"
        })
    return hospital


def _appointment_visible(db: Session, ctx: RequestContext, appointment_id: int) -> Appointment:
    item = db.get(Appointment, appointment_id)
    if item is None:
        raise _not_found()
    if ctx.role == "PATIENT" and item.patient_id != ctx.patient_id:
        raise _not_found()
    elif ctx.role == "DOCTOR" and item.doctor_id != ctx.doctor_id:
        raise _not_found()
    elif ctx.role == "HOSPITAL_ADMIN":
        ctx.require_hospital(item.hospital_id)
    return item


@router.get("/health")
def health():
    return {"status": "ready", "service": "core-api", "version": "2.0"}


@router.post("/auth/register", status_code=201)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail={
            "code": "EMAIL_ALREADY_REGISTERED", "message": "Unable to register account"
        })
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    db.add(user)
    db.flush()
    if payload.role == "PATIENT":
        db.add(Patient(
            user_id=user.id,
            name=payload.full_name,
            email=email,
            communication_preference="EMAIL",
        ))
    db.add(AuditEvent(
        correlation_id=request.state.correlation_id,
        actor_id=user.id,
        action="ACCOUNT_REGISTER",
        resource_type="user",
        resource_id=str(user.id),
        outcome="SUCCEEDED",
    ))
    db.commit()
    return {"id": user.id, "email": user.email, "role": user.role}


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    valid = user is not None and user.is_active and verify_password(payload.password, user.password_hash)
    db.add(AuditEvent(
        correlation_id=request.state.correlation_id,
        actor_id=user.id if user else None,
        action="LOGIN",
        resource_type="user",
        resource_id=str(user.id) if user else None,
        outcome="SUCCEEDED" if valid else "DENIED",
    ))
    db.commit()
    if not valid:
        raise HTTPException(status_code=401, detail={
            "code": "AUTH_REQUIRED", "message": "Invalid email or password"
        })
    return {
        "access_token": create_access_token(user.id, user.role),
        "token_type": "bearer",
        "expires_in": settings.token_ttl_seconds,
    }


@router.get("/auth/me")
def me(ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    user = db.get(User, ctx.user_id)
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "hospital_ids": sorted(ctx.hospital_ids),
        "patient_id": ctx.patient_id,
        "doctor_id": ctx.doctor_id,
    }


@router.post("/hospitals", status_code=201)
def create_hospital(
    payload: HospitalCreateRequest,
    ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")),
    db: Session = Depends(get_db),
):
    platform_id = db.scalar(select(func.min(Platform.id)))
    if platform_id is None:
        platform = Platform(name="Healthcare AI Platform")
        db.add(platform)
        db.flush()
        platform_id = platform.id
    hospital = Hospital(
        platform_id=platform_id,
        name=payload.name,
        address=payload.address,
        contact_email=str(payload.contact_email),
        contact_phone=payload.contact_phone,
        operating_hours_json=_json(payload.operating_hours),
        supported_systems_json=_json(payload.supported_systems),
        status="DRAFT",
    )
    db.add(hospital)
    db.flush()
    db.add(HospitalMembership(
        hospital_id=hospital.id, user_id=ctx.user_id, role="HOSPITAL_ADMIN"
    ))
    record_audit(db, ctx, "HOSPITAL_CREATE", "SUCCEEDED", hospital_id=hospital.id, resource_type="hospital", resource_id=hospital.id)
    db.commit()
    return _entity(hospital)


@router.get("/hospitals")
def list_hospitals(
    ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)
):
    statement = select(Hospital)
    if ctx.role == "PLATFORM_ADMIN":
        pass
    elif ctx.role in {"HOSPITAL_ADMIN", "DOCTOR"}:
        statement = statement.where(Hospital.id.in_(ctx.hospital_ids))
    else:
        statement = statement.where(Hospital.status == "APPROVED")
    items = db.scalars(statement.order_by(Hospital.name)).all()
    if ctx.role in {"PLATFORM_ADMIN", "HOSPITAL_ADMIN", "DOCTOR"}:
        return [_entity(x) for x in items]
    # Public/patient discovery is a deliberately limited projection (D1): no contact
    # phone, operating hours, supported systems or review history by default.
    return [{"id": x.id, "name": x.name, "address": x.address} for x in items]


@router.get("/admin/patients")
def list_patients_for_admin(ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN")), db: Session = Depends(get_db)):
    items = db.scalars(select(Patient).order_by(Patient.name)).all()
    return [{"id": x.id, "name": x.name, "email": x.email, "communication_preference": x.communication_preference} for x in items]


@router.get("/hospitals/{hospital_id}/configuration")
def hospital_configuration(
    hospital_id: int,
    ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN", "HOSPITAL_ADMIN", "DOCTOR")),
    db: Session = Depends(get_db),
):
    hospital = db.get(Hospital, hospital_id)
    if hospital is None:
        raise _not_found()
    if ctx.role != "PLATFORM_ADMIN":
        ctx.require_hospital(hospital_id)
    departments = db.scalars(select(Department).where(Department.hospital_id == hospital_id)).all()
    specialties = db.scalars(select(Specialty).where(Specialty.hospital_id == hospital_id)).all()
    doctors = db.scalars(select(Doctor).where(Doctor.hospital_id == hospital_id)).all()
    types = db.scalars(select(AppointmentType).where(AppointmentType.hospital_id == hospital_id)).all()
    calendars = db.scalars(select(Calendar).where(Calendar.hospital_id == hospital_id)).all()
    questionnaires = db.scalars(select(Questionnaire).where(Questionnaire.hospital_id == hospital_id)).all()
    workflows = db.scalars(select(Workflow).where(Workflow.hospital_id == hospital_id)).all()
    connections = db.scalars(select(HealthcareSystemConnection).where(HealthcareSystemConnection.hospital_id == hospital_id)).all()
    return {
        "hospital": _entity(hospital),
        "departments": [_entity(x) for x in departments],
        "specialties": [_entity(x) for x in specialties],
        "doctors": [_entity(x) for x in doctors],
        "appointment_types": [_entity(x) for x in types],
        "calendars": [_entity(x) for x in calendars],
        "questionnaires": [_entity(x) for x in questionnaires],
        "workflows": [_entity(x) for x in workflows],
        "connections": [_entity(x, exclude={"config_json"}) for x in connections],
    }


@router.post("/hospitals/{hospital_id}/submit")
def submit_hospital(
    hospital_id: int,
    ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")),
    db: Session = Depends(get_db),
):
    hospital = _require_owned_hospital(db, ctx, hospital_id)
    if hospital.status not in {"DRAFT", "CORRECTIONS_REQUIRED", "REJECTED"}:
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATE", "message": "Hospital cannot be submitted"})
    hospital.status = "SUBMITTED"
    hospital.submitted_at = utcnow()
    record_audit(db, ctx, "HOSPITAL_SUBMIT", "SUCCEEDED", hospital_id=hospital.id, resource_type="hospital", resource_id=hospital.id)
    db.commit()
    return _entity(hospital)


@router.post("/admin/hospitals/{hospital_id}/review")
def review_hospital(
    hospital_id: int,
    payload: HospitalReviewRequest,
    ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN")),
    db: Session = Depends(get_db),
):
    hospital = db.get(Hospital, hospital_id)
    if hospital is None:
        raise _not_found()
    if hospital.status not in {"SUBMITTED", "UNDER_REVIEW", "CORRECTIONS_REQUIRED"}:
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATE", "message": "Hospital is not reviewable"})
    hospital.status = payload.decision
    hospital.review_notes = payload.notes
    hospital.reviewed_by = ctx.user_id
    hospital.reviewed_at = utcnow()
    record_audit(db, ctx, "HOSPITAL_REVIEW", "SUCCEEDED", hospital_id=hospital.id, resource_type="hospital", resource_id=hospital.id, metadata={"decision": payload.decision})
    db.commit()
    return _entity(hospital)


@router.post("/admin/hospitals/{hospital_id}/{action}")
def change_hospital_status(
    hospital_id: int,
    action: str,
    ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN")),
    db: Session = Depends(get_db),
):
    hospital = db.get(Hospital, hospital_id)
    if hospital is None:
        raise _not_found()
    mapping = {"suspend": "SUSPENDED", "reactivate": "APPROVED"}
    if action not in mapping:
        raise _not_found()
    hospital.status = mapping[action]
    record_audit(db, ctx, f"HOSPITAL_{action.upper()}", "SUCCEEDED", hospital_id=hospital.id, resource_type="hospital", resource_id=hospital.id)
    db.commit()
    return _entity(hospital)


@router.post("/hospitals/{hospital_id}/departments", status_code=201)
def create_department(hospital_id: int, payload: DepartmentCreateRequest, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    _require_owned_hospital(db, ctx, hospital_id, approved=True)
    item = Department(hospital_id=hospital_id, name=payload.name)
    db.add(item); db.commit(); db.refresh(item)
    return _entity(item)


@router.post("/hospitals/{hospital_id}/specialties", status_code=201)
def create_specialty(hospital_id: int, payload: SpecialtyCreateRequest, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    _require_owned_hospital(db, ctx, hospital_id, approved=True)
    if payload.department_id:
        department = db.get(Department, payload.department_id)
        if not department or department.hospital_id != hospital_id:
            raise _not_found()
    item = Specialty(hospital_id=hospital_id, department_id=payload.department_id, name=payload.name)
    db.add(item); db.commit(); db.refresh(item)
    return _entity(item)


@router.get("/doctors/unlinked-accounts")
def list_unlinked_doctor_accounts(ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    linked_user_ids = select(Doctor.user_id).where(Doctor.user_id.is_not(None))
    users = db.scalars(
        select(User).where(User.role == "DOCTOR", User.is_active, User.id.not_in(linked_user_ids))
        .order_by(User.full_name)
    ).all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name} for u in users]


@router.post("/hospitals/{hospital_id}/doctors", status_code=201)
def create_doctor(hospital_id: int, payload: DoctorCreateRequest, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    _require_owned_hospital(db, ctx, hospital_id, approved=True)
    specialty = db.get(Specialty, payload.specialty_id)
    if not specialty or specialty.hospital_id != hospital_id:
        raise _not_found()
    user_id = None
    if payload.user_email:
        user = db.scalar(select(User).where(User.email == str(payload.user_email).lower()))
        if not user or user.role != "DOCTOR":
            raise HTTPException(status_code=422, detail={"code": "DOCTOR_ACCOUNT_REQUIRED", "message": "Registered doctor account not found"})
        if db.scalar(select(Doctor).where(Doctor.user_id == user.id)) is not None:
            raise HTTPException(status_code=409, detail={"code": "DOCTOR_ALREADY_LINKED", "message": "This account is already linked to a doctor profile"})
        user_id = user.id
        if db.scalar(select(HospitalMembership).where(HospitalMembership.hospital_id == hospital_id, HospitalMembership.user_id == user.id)) is None:
            db.add(HospitalMembership(hospital_id=hospital_id, user_id=user.id, role="DOCTOR"))
    item = Doctor(
        hospital_id=hospital_id, user_id=user_id, department_id=payload.department_id,
        specialty_id=payload.specialty_id, name=payload.name, photo_url=payload.photo_url,
        qualifications=payload.qualifications, experience_years=payload.experience_years,
        languages_json=_json(payload.languages), consultation_types_json=_json(payload.consultation_types),
        external_provider_id=payload.external_provider_id, status="PENDING_APPROVAL",
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "DOCTOR_ALREADY_LINKED", "message": "This account is already linked to a doctor profile"})
    db.refresh(item)
    record_audit(db, ctx, "DOCTOR_INVITE", "SUCCEEDED", hospital_id=hospital_id, resource_type="doctor", resource_id=item.id)
    db.commit()
    return _entity(item)


@router.get("/admin/doctors")
def list_doctors_for_review(status_filter: str | None = None, ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN")), db: Session = Depends(get_db)):
    statement = select(Doctor)
    if status_filter:
        statement = statement.where(Doctor.status == status_filter)
    return [_entity(x) for x in db.scalars(statement.order_by(Doctor.created_at.desc())).all()]


@router.post("/admin/doctors/{doctor_id}/review")
def review_doctor(doctor_id: int, payload: DoctorReviewRequest, ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN")), db: Session = Depends(get_db)):
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise _not_found()
    if doctor.status != "PENDING_APPROVAL":
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATE", "message": "Doctor profile is not pending approval"})
    doctor.status = "ACTIVE" if payload.decision == "APPROVED" else "REJECTED"
    doctor.review_notes = payload.notes
    doctor.reviewed_by = ctx.user_id
    doctor.reviewed_at = utcnow()
    record_audit(db, ctx, "DOCTOR_REVIEW", "SUCCEEDED", hospital_id=doctor.hospital_id, resource_type="doctor", resource_id=doctor.id, metadata={"decision": payload.decision})
    db.commit()
    return _entity(doctor)


@router.post("/admin/doctors/{doctor_id}/{action}")
def change_doctor_status(doctor_id: int, action: str, ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN")), db: Session = Depends(get_db)):
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise _not_found()
    mapping = {"suspend": "SUSPENDED", "reactivate": "ACTIVE"}
    if action not in mapping or doctor.status not in {"ACTIVE", "SUSPENDED", "INACTIVE"}:
        raise _not_found()
    doctor.status = mapping[action]
    record_audit(db, ctx, f"DOCTOR_{action.upper()}", "SUCCEEDED", hospital_id=doctor.hospital_id, resource_type="doctor", resource_id=doctor.id)
    db.commit()
    return _entity(doctor)


@router.get("/doctors")
def discover_doctors(specialty: str | None = None, hospital_id: int | None = None, ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    return CapabilityRunner(db, ctx).search_doctors(specialty=specialty, hospital_id=hospital_id)


@router.get("/doctors/me")
def get_my_doctor_profile(ctx: RequestContext = Depends(require_roles("DOCTOR")), db: Session = Depends(get_db)):
    if ctx.doctor_id is None:
        raise HTTPException(status_code=404, detail={
            "code": "NOT_LINKED", "message": "Your account is not yet linked to a doctor profile by a hospital admin",
        })
    doctor = db.get(Doctor, ctx.doctor_id)
    return {
        **_entity(doctor),
        "languages": json.loads(doctor.languages_json or "[]"),
        "consultation_types": json.loads(doctor.consultation_types_json or "[]"),
    }


@router.get("/appointment-types")
def discover_appointment_types(hospital_id: int, specialty_id: int | None = None, ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    statement = select(AppointmentType).where(AppointmentType.hospital_id == hospital_id, AppointmentType.status == "ACTIVE")
    if specialty_id is not None:
        statement = statement.where(AppointmentType.specialty_id.in_((specialty_id, None)))
    return [_entity(x) for x in db.scalars(statement.order_by(AppointmentType.name)).all()]


@router.post("/hospitals/{hospital_id}/appointment-types", status_code=201)
def create_appointment_type(hospital_id: int, payload: AppointmentTypeCreateRequest, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    _require_owned_hospital(db, ctx, hospital_id, approved=True)
    item = AppointmentType(hospital_id=hospital_id, specialty_id=payload.specialty_id, name=payload.name, duration_minutes=payload.duration_minutes)
    db.add(item); db.commit(); db.refresh(item)
    return _entity(item)


@router.post("/hospitals/{hospital_id}/calendars", status_code=201)
def create_calendar(hospital_id: int, payload: CalendarCreateRequest, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    _require_owned_hospital(db, ctx, hospital_id, approved=True)
    doctor, kind = db.get(Doctor, payload.doctor_id), db.get(AppointmentType, payload.appointment_type_id)
    if not doctor or not kind or doctor.hospital_id != hospital_id or kind.hospital_id != hospital_id:
        raise _not_found()
    if doctor.status != "ACTIVE":
        raise HTTPException(status_code=409, detail={"code": "DOCTOR_NOT_ACTIVE", "message": "Doctor must be approved and active before a calendar can be created"})
    if db.scalar(select(Calendar).where(Calendar.doctor_id == doctor.id, Calendar.appointment_type_id == kind.id)) is not None:
        raise HTTPException(status_code=409, detail={"code": "CALENDAR_ALREADY_EXISTS", "message": "This doctor already has a calendar for that appointment type"})
    item = Calendar(hospital_id=hospital_id, doctor_id=doctor.id, appointment_type_id=kind.id, timezone=payload.timezone)
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "CALENDAR_ALREADY_EXISTS", "message": "This doctor already has a calendar for that appointment type"})
    db.refresh(item)
    return _entity(item)


def _calendar_owned(db: Session, ctx: RequestContext, calendar_id: int) -> Calendar:
    item = db.get(Calendar, calendar_id)
    if item is None:
        raise _not_found()
    if ctx.role == "DOCTOR" and item.doctor_id != ctx.doctor_id:
        raise _not_found()
    if ctx.role == "HOSPITAL_ADMIN":
        ctx.require_hospital(item.hospital_id)
    return item


@router.get("/calendars")
def list_calendars(doctor_id: int | None = None, hospital_id: int | None = None, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN", "DOCTOR")), db: Session = Depends(get_db)):
    statement = select(Calendar)
    if ctx.role == "DOCTOR":
        statement = statement.where(Calendar.doctor_id == ctx.doctor_id)
    else:
        statement = statement.where(Calendar.hospital_id.in_(ctx.hospital_ids))
        if hospital_id is not None:
            ctx.require_hospital(hospital_id)
            statement = statement.where(Calendar.hospital_id == hospital_id)
        if doctor_id is not None:
            statement = statement.where(Calendar.doctor_id == doctor_id)
    return [_entity(x) for x in db.scalars(statement).all()]


@router.get("/calendars/{calendar_id}/working-hours")
def list_working_hours(calendar_id: int, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN", "DOCTOR")), db: Session = Depends(get_db)):
    _calendar_owned(db, ctx, calendar_id)
    items = db.scalars(select(WorkingHours).where(WorkingHours.calendar_id == calendar_id).order_by(WorkingHours.weekday, WorkingHours.start_time)).all()
    return [_entity(x) for x in items]


@router.post("/calendars/{calendar_id}/working-hours", status_code=201)
def create_working_hours(calendar_id: int, payload: WorkingHoursCreateRequest, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN", "DOCTOR")), db: Session = Depends(get_db)):
    _calendar_owned(db, ctx, calendar_id)
    existing = db.scalars(select(WorkingHours).where(
        WorkingHours.calendar_id == calendar_id, WorkingHours.weekday == payload.weekday,
    )).all()
    overlap = next(
        (x for x in existing if payload.start_time < x.end_time and payload.end_time > x.start_time),
        None,
    )
    if overlap is not None:
        raise HTTPException(status_code=409, detail={
            "code": "WORKING_HOURS_OVERLAP",
            "message": f"Overlaps with existing working hours {overlap.start_time}–{overlap.end_time} on that day",
        })
    item = WorkingHours(calendar_id=calendar_id, weekday=payload.weekday, start_time=payload.start_time, end_time=payload.end_time)
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "code": "WORKING_HOURS_DUPLICATE", "message": "Identical working hours already exist for that day",
        })
    db.refresh(item)
    return _entity(item)


@router.get("/calendars/{calendar_id}/blocked-periods")
def list_blocked_periods(calendar_id: int, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN", "DOCTOR")), db: Session = Depends(get_db)):
    _calendar_owned(db, ctx, calendar_id)
    items = db.scalars(select(BlockedPeriod).where(BlockedPeriod.calendar_id == calendar_id).order_by(BlockedPeriod.starts_at)).all()
    return [_entity(x) for x in items]


@router.post("/calendars/{calendar_id}/blocked-periods", status_code=201)
def create_block(calendar_id: int, payload: BlockedPeriodCreateRequest, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN", "DOCTOR")), db: Session = Depends(get_db)):
    _calendar_owned(db, ctx, calendar_id)
    item = BlockedPeriod(calendar_id=calendar_id, starts_at=payload.starts_at, ends_at=payload.ends_at, reason=payload.reason, kind=payload.kind)
    db.add(item); db.commit(); db.refresh(item)
    return _entity(item)


@router.get("/availability")
def availability(doctor_id: int, appointment_type_id: int, date_from: str, date_to: str, ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    query = AvailabilityQuery(doctor_id=doctor_id, appointment_type_id=appointment_type_id, date_from=date_from, date_to=date_to)
    return list_available_slots(db, doctor_id=query.doctor_id, appointment_type_id=query.appointment_type_id, date_from=query.date_from, date_to=query.date_to)


@router.get("/patients/me")
def get_patient(ctx: RequestContext = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)):
    patient = db.get(Patient, ctx.patient_id)
    preferences = db.scalars(select(UserPreference).where(UserPreference.patient_id == ctx.patient_id)).all()
    return {**_entity(patient), "preferences": {p.key: json.loads(p.value_json) for p in preferences}}


@router.patch("/patients/me")
def update_patient(payload: PatientProfileUpdateRequest, ctx: RequestContext = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)):
    patient = db.get(Patient, ctx.patient_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(patient, key, value)
    record_audit(db, ctx, "PATIENT_PROFILE_UPDATE", "SUCCEEDED", resource_type="patient", resource_id=patient.id)
    db.commit()
    return _entity(patient)


@router.put("/patients/me/preferences")
def update_preference(payload: PreferenceRequest, ctx: RequestContext = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)):
    item = db.scalar(select(UserPreference).where(UserPreference.patient_id == ctx.patient_id, UserPreference.key == payload.key))
    if item is None:
        item = UserPreference(patient_id=ctx.patient_id, key=payload.key, value_json=_json(payload.value)); db.add(item)
    else:
        item.value_json = _json(payload.value)
    db.commit()
    return {"key": item.key, "value": payload.value}


@router.post("/hospitals/{hospital_id}/connections", status_code=201)
def create_connection(hospital_id: int, payload: HealthcareConnectionRequest, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    _require_owned_hospital(db, ctx, hospital_id, approved=True)
    item = HealthcareSystemConnection(hospital_id=hospital_id, connector_type=payload.connector_type, base_url=payload.base_url, config_json=_json(payload.config), status=payload.status)
    db.add(item); db.commit(); db.refresh(item)
    return _entity(item, exclude={"config_json"})


@router.post("/appointments", status_code=201)
def book(payload: AppointmentCreateRequest, background: BackgroundTasks, response: Response, ctx: RequestContext = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)):
    result = CapabilityRunner(db, ctx).create_appointment(**payload.model_dump())
    if result["status"] == "RECONCILIATION_REQUIRED":
        response.status_code = status.HTTP_202_ACCEPTED
    background.add_task(_run_worker_once, ctx.correlation_id)
    return result


def _run_worker_once(correlation_id: str):
    with SessionLocal() as worker_db:
        process_due_workflows(worker_db, correlation_id)


@router.get("/appointments")
def list_appointments(ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    statement = select(Appointment)
    if ctx.role == "PATIENT":
        statement = statement.where(Appointment.patient_id == ctx.patient_id)
    elif ctx.role == "DOCTOR":
        statement = statement.where(Appointment.doctor_id == ctx.doctor_id)
    elif ctx.role == "HOSPITAL_ADMIN":
        statement = statement.where(Appointment.hospital_id.in_(ctx.hospital_ids))
    items = db.scalars(statement.order_by(Appointment.starts_at.desc())).all()
    return [{**_entity(x), "starts_at": as_utc_iso(x.starts_at), "ends_at": as_utc_iso(x.ends_at)} for x in items]


@router.get("/appointments/{appointment_id}")
def get_appointment(appointment_id: int, ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    item = _appointment_visible(db, ctx, appointment_id)
    history = db.scalars(select(AppointmentHistory).where(AppointmentHistory.appointment_id == item.id).order_by(AppointmentHistory.changed_at)).all()
    return {**_entity(item), "starts_at": as_utc_iso(item.starts_at), "ends_at": as_utc_iso(item.ends_at), "history": [_entity(x) for x in history]}


@router.post("/appointments/{appointment_id}/cancel")
def cancel(appointment_id: int, payload: AppointmentCancelRequest, background: BackgroundTasks, ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    result = cancel_appointment(db, ctx, _appointment_visible(db, ctx, appointment_id), **payload.model_dump())
    background.add_task(_run_worker_once, ctx.correlation_id)
    return result


@router.post("/appointments/{appointment_id}/reschedule")
def reschedule(appointment_id: int, payload: AppointmentRescheduleRequest, background: BackgroundTasks, ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    result = reschedule_appointment(db, ctx, _appointment_visible(db, ctx, appointment_id), **payload.model_dump())
    background.add_task(_run_worker_once, ctx.correlation_id)
    return result


def _questionnaire_selector_collision(db: Session, item: Questionnaire) -> Questionnaire | None:
    """D6: at most one ACTIVE questionnaire may exist per exact selector. Used both when
    directly creating an ACTIVE questionnaire and when publishing a draft."""
    return db.scalar(select(Questionnaire).where(
        Questionnaire.hospital_id == item.hospital_id,
        Questionnaire.status == "ACTIVE",
        Questionnaire.id != item.id,
        Questionnaire.doctor_id.is_(item.doctor_id) if item.doctor_id is None else Questionnaire.doctor_id == item.doctor_id,
        Questionnaire.specialty_id.is_(item.specialty_id) if item.specialty_id is None else Questionnaire.specialty_id == item.specialty_id,
        Questionnaire.appointment_type_id.is_(item.appointment_type_id) if item.appointment_type_id is None else Questionnaire.appointment_type_id == item.appointment_type_id,
    ))


@router.post("/hospitals/{hospital_id}/questionnaires", status_code=201)
def create_questionnaire(hospital_id: int, payload: QuestionnaireCreateRequest, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    _require_owned_hospital(db, ctx, hospital_id, approved=True)
    item = Questionnaire(hospital_id=hospital_id, title=payload.title, specialty_id=payload.specialty_id, doctor_id=payload.doctor_id, appointment_type_id=payload.appointment_type_id, approved_category=payload.approved_category, status="DRAFT")
    db.add(item); db.flush()
    for question in payload.questions:
        db.add(QuestionnaireQuestion(questionnaire_id=item.id, position=question.position, prompt=question.prompt, question_type=question.question_type, required=question.required, options_json=_json(question.options), validation_json=_json(question.validation)))
    if payload.status == "ACTIVE":
        collision = _questionnaire_selector_collision(db, item)
        if collision is not None:
            db.rollback()
            raise HTTPException(status_code=409, detail={
                "code": "QUESTIONNAIRE_SELECTOR_COLLISION",
                "message": f"Questionnaire #{collision.id} '{collision.title}' already covers this exact selector",
            })
        item.status = "ACTIVE"
        item.published_by_user_id = ctx.user_id
        item.published_at = utcnow()
    db.commit(); db.refresh(item)
    return _entity(item)


@router.post("/hospitals/{hospital_id}/questionnaires/propose", status_code=201)
def propose_questionnaire(hospital_id: int, payload: QuestionnaireProposeRequest, ctx: RequestContext = Depends(require_roles("DOCTOR")), db: Session = Depends(get_db)):
    ctx.require_hospital(hospital_id)
    if ctx.doctor_id is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_LINKED", "message": "Your account is not linked to a doctor profile"})
    item = Questionnaire(
        hospital_id=hospital_id, title=payload.title, specialty_id=payload.specialty_id,
        doctor_id=ctx.doctor_id, appointment_type_id=payload.appointment_type_id,
        approved_category=payload.approved_category, status="DRAFT",
        proposed_by_user_id=ctx.user_id,
    )
    db.add(item); db.flush()
    for question in payload.questions:
        db.add(QuestionnaireQuestion(questionnaire_id=item.id, position=question.position, prompt=question.prompt, question_type=question.question_type, required=question.required, options_json=_json(question.options), validation_json=_json(question.validation)))
    record_audit(db, ctx, "QUESTIONNAIRE_PROPOSE", "SUCCEEDED", hospital_id=hospital_id, resource_type="questionnaire", resource_id=item.id)
    db.commit(); db.refresh(item)
    return _entity(item)


@router.get("/hospitals/{hospital_id}/questionnaires/drafts")
def list_questionnaire_drafts(hospital_id: int, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN", "DOCTOR")), db: Session = Depends(get_db)):
    ctx.require_hospital(hospital_id)
    statement = select(Questionnaire).where(Questionnaire.hospital_id == hospital_id, Questionnaire.status == "DRAFT")
    if ctx.role == "DOCTOR":
        statement = statement.where(Questionnaire.proposed_by_user_id == ctx.user_id)
    return [_entity(x) for x in db.scalars(statement.order_by(Questionnaire.created_at.desc())).all()]


@router.post("/hospitals/{hospital_id}/questionnaires/{questionnaire_id}/publish")
def publish_questionnaire(hospital_id: int, questionnaire_id: int, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    _require_owned_hospital(db, ctx, hospital_id, approved=True)
    item = db.get(Questionnaire, questionnaire_id)
    if item is None or item.hospital_id != hospital_id:
        raise _not_found()
    if item.status != "DRAFT":
        raise HTTPException(status_code=409, detail={"code": "INVALID_STATE", "message": "Only a draft questionnaire can be published"})
    collision = _questionnaire_selector_collision(db, item)
    if collision is not None:
        raise HTTPException(status_code=409, detail={
            "code": "QUESTIONNAIRE_SELECTOR_COLLISION",
            "message": f"Questionnaire #{collision.id} '{collision.title}' already covers this exact selector",
        })
    item.status = "ACTIVE"
    item.published_by_user_id = ctx.user_id
    item.published_at = utcnow()
    record_audit(db, ctx, "QUESTIONNAIRE_PUBLISH", "SUCCEEDED", hospital_id=hospital_id, resource_type="questionnaire", resource_id=item.id)
    db.commit(); db.refresh(item)
    return _entity(item)


@router.get("/questionnaire-assignments")
def list_assignments(ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    statement = select(QuestionnaireAssignment)
    if ctx.role == "PATIENT":
        statement = statement.where(QuestionnaireAssignment.patient_id == ctx.patient_id)
    elif ctx.role == "DOCTOR":
        statement = statement.join(Appointment, Appointment.id == QuestionnaireAssignment.appointment_id).where(Appointment.doctor_id == ctx.doctor_id)
    elif ctx.role == "HOSPITAL_ADMIN":
        statement = statement.join(Appointment, Appointment.id == QuestionnaireAssignment.appointment_id).where(Appointment.hospital_id.in_(ctx.hospital_ids))
    return [_entity(x) for x in db.scalars(statement).all()]


@router.get("/questionnaire-assignments/{assignment_id}")
def get_assignment(assignment_id: int, ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    assignment = db.get(QuestionnaireAssignment, assignment_id)
    if not assignment:
        raise _not_found()
    appointment = _appointment_visible(db, ctx, assignment.appointment_id)
    questions = db.scalars(select(QuestionnaireQuestion).where(
        QuestionnaireQuestion.questionnaire_id == assignment.questionnaire_id
    ).order_by(QuestionnaireQuestion.position)).all()
    return {
        "assignment": _entity(assignment),
        "questions": [{**_entity(x), "options": json.loads(x.options_json), "validation": json.loads(x.validation_json)} for x in questions],
    }


def _validate_answer(question: QuestionnaireQuestion, value):
    options, rules = json.loads(question.options_json), json.loads(question.validation_json)
    kind = question.question_type
    if kind == "YES_NO" and not isinstance(value, bool):
        raise ValueError("expected yes/no")
    if kind == "CHOICE" and value not in options:
        raise ValueError("invalid choice")
    if kind == "MULTIPLE_CHOICE" and (not isinstance(value, list) or any(x not in options for x in value)):
        raise ValueError("invalid multiple choice")
    if kind == "NUMERIC":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("expected numeric answer")
        if "min" in rules and value < rules["min"] or "max" in rules and value > rules["max"]:
            raise ValueError("numeric answer outside allowed range")
    if kind == "DATE":
        datetime.fromisoformat(str(value))
    if kind in {"SHORT_TEXT", "LONG_TEXT"} and not isinstance(value, str):
        raise ValueError("expected text answer")


@router.post("/questionnaire-assignments/{assignment_id}/answers")
def submit_answers(assignment_id: int, payload: QuestionnaireAnswersRequest, ctx: RequestContext = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)):
    assignment = db.get(QuestionnaireAssignment, assignment_id)
    if not assignment or assignment.patient_id != ctx.patient_id:
        raise _not_found()
    questions = db.scalars(select(QuestionnaireQuestion).where(QuestionnaireQuestion.questionnaire_id == assignment.questionnaire_id)).all()
    question_map = {x.id: x for x in questions}
    missing = [x.id for x in questions if x.required and x.id not in payload.answers]
    if missing:
        raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "Required questionnaire answers are missing", "question_ids": missing})
    for question_id, value in payload.answers.items():
        question = question_map.get(question_id)
        if not question:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "Question is not part of the approved questionnaire"})
        try:
            _validate_answer(question, value)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": f"Question {question_id}: {exc}"}) from exc
        existing = db.scalar(select(QuestionnaireResponse).where(QuestionnaireResponse.assignment_id == assignment.id, QuestionnaireResponse.question_id == question_id))
        if existing: existing.answer_json = _json(value)
        else: db.add(QuestionnaireResponse(assignment_id=assignment.id, question_id=question_id, answer_json=_json(value)))
    assignment.status = "COMPLETED"
    record_audit(db, ctx, "QUESTIONNAIRE_SUBMIT", "SUCCEEDED", resource_type="questionnaire_assignment", resource_id=assignment.id)
    db.commit()
    return {"assignment_id": assignment.id, "status": assignment.status}


@router.get("/questionnaire-assignments/{assignment_id}/responses")
def view_answers(assignment_id: int, ctx: RequestContext = Depends(require_roles("DOCTOR")), db: Session = Depends(get_db)):
    assignment = db.get(QuestionnaireAssignment, assignment_id)
    if not assignment: raise _not_found()
    appointment = _appointment_visible(db, ctx, assignment.appointment_id)
    responses = db.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.assignment_id == assignment.id)).all()
    record_audit(db, ctx, "QUESTIONNAIRE_RESPONSE_VIEW", "SUCCEEDED", hospital_id=appointment.hospital_id, resource_type="questionnaire_assignment", resource_id=assignment.id)
    db.commit()
    return [{"question_id": x.question_id, "answer": json.loads(x.answer_json)} for x in responses]


@router.post("/hospitals/{hospital_id}/workflows", status_code=201)
def create_workflow(hospital_id: int, payload: WorkflowCreateRequest, ctx: RequestContext = Depends(require_roles("HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    _require_owned_hospital(db, ctx, hospital_id, approved=True)
    item = Workflow(hospital_id=hospital_id, name=payload.name, trigger_event=payload.trigger_event, definition_json=_json(payload.definition))
    db.add(item); db.commit(); db.refresh(item)
    return _entity(item)


@router.post("/operations/workflows/run")
def run_workflows(ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN", "HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    return {"processed_execution_ids": process_due_workflows(db, ctx.correlation_id)}


@router.post("/agent/messages")
def agent_message(payload: AgentMessageRequest, ctx: RequestContext = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)):
    return process_message(db, ctx, message=payload.message, conversation_id=payload.conversation_id)


@router.post("/voice/turn")
def voice_turn(payload: VoiceTurnRequest, ctx: RequestContext = Depends(require_roles("PATIENT")), db: Session = Depends(get_db)):
    result = process_message(db, ctx, message=payload.transcript, conversation_id=payload.conversation_id)
    return {"turn_states": ["listening", "transcribing", "thinking", "executing", "speaking"], **result}


@router.post("/telephone/events")
def telephone_event(payload: TelephoneEventRequest, request: Request, db: Session = Depends(get_db)):
    if settings.telephone_webhook_secret:
        supplied = request.headers.get("x-telephone-webhook-secret", "")
        if not hmac.compare_digest(supplied, settings.telephone_webhook_secret):
            raise HTTPException(status_code=401, detail={"code": "INVALID_WEBHOOK", "message": "Invalid telephone webhook"})
    if payload.event != "TRANSCRIPT" or not payload.patient_email or not payload.transcript:
        return {"call_id": payload.call_id, "status": payload.event}
    user = db.scalar(select(User).where(User.email == str(payload.patient_email).lower(), User.role == "PATIENT"))
    patient = db.scalar(select(Patient).where(Patient.user_id == user.id)) if user else None
    if not user or not patient:
        return {"call_id": payload.call_id, "status": "IDENTIFICATION_REQUIRED", "prompt": "Please verify your patient account with an operator."}
    ctx = RequestContext(user_id=user.id, role=user.role, hospital_ids=frozenset(), patient_id=patient.id, doctor_id=None, correlation_id=request.state.correlation_id)
    result = process_message(db, ctx, message=payload.transcript, conversation_id=f"call-{payload.call_id}")
    return {"call_id": payload.call_id, "status": "RESPOND", **result}


@router.post("/webhooks/resend")
def resend_webhook(payload: dict, db: Session = Depends(get_db)):
    notification = apply_resend_webhook(db, payload)
    db.commit()
    return {"accepted": True, "notification_id": notification.id if notification else None}


@router.get("/operations/traces/{correlation_id}")
def operation_trace(correlation_id: str, ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN", "HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    events = db.scalars(select(OperationalEvent).where(OperationalEvent.correlation_id == correlation_id).order_by(OperationalEvent.occurred_at)).all()
    if ctx.role == "HOSPITAL_ADMIN" and any(x.hospital_id not in ctx.hospital_ids for x in events if x.hospital_id):
        raise _not_found()
    return {"correlation_id": correlation_id, "events": [_entity(x) for x in events]}


@router.get("/operations/audit")
def audit(ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN", "HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    statement = select(AuditEvent)
    if ctx.role == "HOSPITAL_ADMIN": statement = statement.where(AuditEvent.hospital_id.in_(ctx.hospital_ids))
    return [_entity(x) for x in db.scalars(statement.order_by(AuditEvent.occurred_at.desc()).limit(500)).all()]


@router.get("/operations/reconciliation")
def reconciliation(ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN", "HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    statement = select(ReconciliationRecord)
    if ctx.role == "HOSPITAL_ADMIN": statement = statement.where(ReconciliationRecord.hospital_id.in_(ctx.hospital_ids))
    return [_entity(x) for x in db.scalars(statement.order_by(ReconciliationRecord.created_at.desc())).all()]


def _reconciliation_owned(db: Session, ctx: RequestContext, record_id: int) -> ReconciliationRecord:
    item = db.get(ReconciliationRecord, record_id)
    if not item: raise _not_found()
    if ctx.role == "HOSPITAL_ADMIN": ctx.require_hospital(item.hospital_id)
    return item


def _reconciliation_evidence(db: Session, item: ReconciliationRecord) -> dict:
    """Query the EHR fresh (not trust the operator's word) for the current record.

    Returns {"external_status": ..., "matches_local": bool, "detail": str}. Never
    mutates appointment/reconciliation state; callers decide what to do with it.
    """
    operation = db.get(IntegrationOperation, item.operation_id)
    appointment = db.get(Appointment, item.appointment_id) if item.appointment_id else None
    if operation is None or appointment is None:
        return {"external_status": "UNKNOWN", "matches_local": False, "detail": "Missing operation or appointment reference"}
    connection = db.get(HealthcareSystemConnection, operation.connection_id)
    if connection is None or connection.status != "ACTIVE":
        return {"external_status": "UNKNOWN", "matches_local": False, "detail": "Integration connection is not active"}
    connector = _connector(connection)
    external_id = operation.external_id
    if not external_id:
        searched = connector.search_appointment(
            patient_id=appointment.patient_id, doctor_id=appointment.doctor_id,
            date=appointment.starts_at.date().isoformat(),
            time=appointment.starts_at.time().replace(microsecond=0).isoformat(timespec="minutes"),
        )
        if not searched.succeeded:
            return {"external_status": searched.classification, "matches_local": False, "detail": searched.message or "No external record found by search"}
        external_id = searched.data.get("external_id")
    verified = connector.verify_appointment(
        external_id=external_id,
        expected={
            "patient_id": appointment.patient_id, "doctor_id": appointment.doctor_id,
            "date": appointment.starts_at.date().isoformat(),
            "time": appointment.starts_at.time().replace(microsecond=0).isoformat(timespec="minutes"),
            "status": "CONFIRMED",
        },
    )
    db.add(IntegrationVerification(
        operation_id=operation.id, status=verified.classification,
        evidence_json=_json(verified.data), verified_at=utcnow(),
    ))
    return {
        "external_status": verified.classification,
        "matches_local": verified.succeeded,
        "detail": verified.message or ("Matched" if verified.succeeded else "External record did not match expected fields"),
    }


@router.post("/operations/reconciliation/{record_id}/verify")
def verify_reconciliation(record_id: int, ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN", "HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    item = _reconciliation_owned(db, ctx, record_id)
    evidence = _reconciliation_evidence(db, item)
    record_audit(db, ctx, "RECONCILIATION_VERIFY", "SUCCEEDED", hospital_id=item.hospital_id, resource_type="reconciliation", resource_id=item.id, metadata=evidence)
    db.commit()
    return evidence


@router.post("/operations/reconciliation/{record_id}/resolve")
def resolve_reconciliation(record_id: int, payload: ReconciliationResolveRequest, ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN", "HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    item = _reconciliation_owned(db, ctx, record_id)
    if item.status == "RESOLVED":
        raise HTTPException(status_code=409, detail={"code": "ALREADY_RESOLVED", "message": "This reconciliation record is already resolved"})
    appointment = db.get(Appointment, item.appointment_id) if item.appointment_id else None
    if payload.final_state == "CONFIRMED":
        # Never take the operator's word for a positive external outcome: re-query the
        # EHR now and only allow CONFIRMED if fresh evidence actually supports it.
        evidence = _reconciliation_evidence(db, item)
        if not evidence["matches_local"]:
            record_audit(db, ctx, "RECONCILIATION_RESOLVE", "DENIED", hospital_id=item.hospital_id, resource_type="reconciliation", resource_id=item.id, metadata={"requested_final_state": "CONFIRMED", "evidence": evidence})
            db.commit()
            raise HTTPException(status_code=409, detail={
                "code": "VERIFICATION_REQUIRED",
                "message": "Cannot confirm without matching external evidence. Fresh verification result: " + evidence["detail"],
                "evidence": evidence,
            })
        if appointment:
            appointment.status = "CONFIRMED"
            appointment.version += 1
    elif appointment:
        # FAILED/CANCELLED never claim a successful booking, so they carry no risk of a
        # false positive and can be recorded on the operator's own authority.
        appointment.status = payload.final_state
        appointment.version += 1
    item.status = "RESOLVED"; item.resolution = payload.resolution; item.owner_user_id = ctx.user_id; item.resolved_at = utcnow()
    record_audit(db, ctx, "RECONCILIATION_RESOLVE", "SUCCEEDED", hospital_id=item.hospital_id, resource_type="reconciliation", resource_id=item.id, metadata={"final_state": payload.final_state})
    db.commit()
    return _entity(item)


@router.get("/operations/metrics")
def metrics(ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN", "HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    hospital_filter = None if ctx.role == "PLATFORM_ADMIN" else ctx.hospital_ids
    def count(model, condition=None):
        statement = select(func.count()).select_from(model)
        if hospital_filter is not None and hasattr(model, "hospital_id"): statement = statement.where(model.hospital_id.in_(hospital_filter))
        if condition is not None: statement = statement.where(condition)
        return db.scalar(statement) or 0
    return {
        "appointments": {"total": count(Appointment), "confirmed": count(Appointment, Appointment.status == "CONFIRMED"), "cancelled": count(Appointment, Appointment.status == "CANCELLED")},
        "ai": {"capability_executions": count(CapabilityExecution), "escalations": count(HumanEscalation)},
        "integrations": {"operations": count(IntegrationOperation), "unknown_or_reconciliation": count(IntegrationOperation, IntegrationOperation.status.in_(["UNKNOWN_OUTCOME", "RECONCILIATION_REQUIRED"]))},
        "workflows": {"running": count(WorkflowExecution, WorkflowExecution.status.in_(["PENDING", "RUNNING", "RETRYING"])), "failed": count(WorkflowExecution, WorkflowExecution.status == "FAILED")},
        "notifications": {"total": count(Notification), "failed": count(Notification, Notification.status == "FAILED")},
    }


@router.get("/operations/notifications")
def list_notifications(ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN", "HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    statement = select(Notification)
    if ctx.role == "HOSPITAL_ADMIN":
        statement = statement.where(Notification.hospital_id.in_(ctx.hospital_ids))
    return [_entity(x, exclude={"template_data_json"}) for x in db.scalars(statement.order_by(Notification.created_at.desc()).limit(500)).all()]


@router.post("/ai/evaluations", status_code=201)
def create_evaluation(payload: EvaluationRequest, ctx: RequestContext = Depends(require_roles("PLATFORM_ADMIN", "HOSPITAL_ADMIN")), db: Session = Depends(get_db)):
    item = AIEvaluation(conversation_id=payload.conversation_id, category=payload.category, passed=payload.passed, score=payload.score, details_json=_json(payload.details))
    db.add(item); db.commit(); db.refresh(item)
    return _entity(item)


@router.get("/dashboards/{role}")
def dashboard(role: str, ctx: RequestContext = Depends(get_current_context), db: Session = Depends(get_db)):
    if role.upper() != ctx.role:
        raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Dashboard is not permitted"})
    data = {"role": ctx.role}
    if ctx.role == "PATIENT":
        data.update({"appointments": list_appointments(ctx, db), "questionnaires": list_assignments(ctx, db)})
    elif ctx.role == "DOCTOR":
        data.update({"appointments": list_appointments(ctx, db), "doctor_id": ctx.doctor_id})
    elif ctx.role == "HOSPITAL_ADMIN":
        data.update({"hospital_ids": sorted(ctx.hospital_ids), "appointments": list_appointments(ctx, db), "metrics": metrics(ctx, db)})
    else:
        data.update({"hospitals": [_entity(x) for x in db.scalars(select(Hospital)).all()], "metrics": metrics(ctx, db)})
    return data


async def voice_websocket(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401); return
    try:
        payload = decode_access_token(token)
    except HTTPException:
        await websocket.close(code=4401); return
    await websocket.accept()
    conversation_id = None
    try:
        while True:
            incoming = await websocket.receive_json()
            if incoming.get("type") == "interrupt":
                await websocket.send_json({"state": "listening", "interrupted": True}); continue
            transcript = str(incoming.get("transcript", "")).strip()
            if not transcript:
                await websocket.send_json({"state": "silence", "prompt": "I did not hear anything. Please try again."}); continue
            await websocket.send_json({"state": "transcribing"})
            with SessionLocal() as db:
                user = db.get(User, int(payload["sub"])); patient = db.scalar(select(Patient).where(Patient.user_id == user.id)) if user else None
                if not user or not patient:
                    await websocket.send_json({"state": "error", "message": "Patient account required"}); continue
                ctx = RequestContext(user_id=user.id, role=user.role, hospital_ids=frozenset(), patient_id=patient.id, doctor_id=None, correlation_id=uuid.uuid4().hex)
                await websocket.send_json({"state": "thinking"})
                result = process_message(db, ctx, message=transcript, conversation_id=conversation_id)
                conversation_id = result.get("conversation_id")
            await websocket.send_json({"state": "speaking", **result})
    except WebSocketDisconnect:
        return
