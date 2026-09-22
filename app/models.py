from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Platform(Base, TimestampMixin):
    __tablename__ = "platforms"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(40), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Hospital(Base, TimestampMixin):
    __tablename__ = "hospitals"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    address: Mapped[str] = mapped_column(Text)
    contact_email: Mapped[str] = mapped_column(String(320))
    contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    operating_hours_json: Mapped[str] = mapped_column(Text, default="{}")
    supported_systems_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(40), default="DRAFT", index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_notes: Mapped[str | None] = mapped_column(Text)


class HospitalMembership(Base, TimestampMixin):
    __tablename__ = "hospital_memberships"
    __table_args__ = (UniqueConstraint("hospital_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")


class Department(Base, TimestampMixin):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("hospital_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")


class Specialty(Base, TimestampMixin):
    __tablename__ = "specialties"
    __table_args__ = (UniqueConstraint("hospital_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")


class Doctor(Base, TimestampMixin):
    __tablename__ = "doctors"
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    specialty_id: Mapped[int] = mapped_column(ForeignKey("specialties.id"), index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    photo_url: Mapped[str | None] = mapped_column(Text)
    qualifications: Mapped[str | None] = mapped_column(Text)
    experience_years: Mapped[int | None] = mapped_column(Integer)
    languages_json: Mapped[str] = mapped_column(Text, default="[]")
    consultation_types_json: Mapped[str] = mapped_column(Text, default="[]")
    external_provider_id: Mapped[str | None] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING_APPROVAL", index=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[str | None] = mapped_column(Text)


class AppointmentType(Base, TimestampMixin):
    __tablename__ = "appointment_types"
    __table_args__ = (UniqueConstraint("hospital_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    specialty_id: Mapped[int | None] = mapped_column(ForeignKey("specialties.id"))
    name: Mapped[str] = mapped_column(String(160))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")


class Calendar(Base, TimestampMixin):
    __tablename__ = "calendars"
    __table_args__ = (UniqueConstraint("doctor_id", "appointment_type_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    appointment_type_id: Mapped[int] = mapped_column(
        ForeignKey("appointment_types.id"), index=True
    )
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")


class WorkingHours(Base, TimestampMixin):
    __tablename__ = "working_hours"
    __table_args__ = (UniqueConstraint("calendar_id", "weekday", "start_time"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[object] = mapped_column(Time)
    end_time: Mapped[object] = mapped_column(Time)


class BlockedPeriod(Base, TimestampMixin):
    __tablename__ = "blocked_periods"
    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(240))
    kind: Mapped[str] = mapped_column(String(30), default="BLOCK")


class Patient(Base, TimestampMixin):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    date_of_birth: Mapped[object | None] = mapped_column(Date)
    communication_preference: Mapped[str] = mapped_column(String(30), default="EMAIL")


class UserPreference(Base, TimestampMixin):
    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("patient_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    value_json: Mapped[str] = mapped_column(Text)


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"
    __table_args__ = (
        Index("ix_appointments_hospital_start", "hospital_id", "starts_at"),
        Index("ix_appointments_doctor_start", "doctor_id", "starts_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id"), index=True)
    appointment_type_id: Mapped[int] = mapped_column(ForeignKey("appointment_types.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), default="REQUESTED", index=True)
    external_id: Mapped[str | None] = mapped_column(String(160), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class SlotReservation(Base):
    __tablename__ = "slot_reservations"
    __table_args__ = (UniqueConstraint("doctor_id", "starts_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id"), unique=True, index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppointmentHistory(Base):
    __tablename__ = "appointment_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Questionnaire(Base, TimestampMixin):
    __tablename__ = "questionnaires"
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    specialty_id: Mapped[int | None] = mapped_column(ForeignKey("specialties.id"))
    doctor_id: Mapped[int | None] = mapped_column(ForeignKey("doctors.id"))
    appointment_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointment_types.id")
    )
    title: Mapped[str] = mapped_column(String(200))
    approved_category: Mapped[str | None] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    proposed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    published_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuestionnaireQuestion(Base, TimestampMixin):
    __tablename__ = "questionnaire_questions"
    __table_args__ = (UniqueConstraint("questionnaire_id", "position"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    questionnaire_id: Mapped[int] = mapped_column(
        ForeignKey("questionnaires.id"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text)
    question_type: Mapped[str] = mapped_column(String(40))
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    options_json: Mapped[str] = mapped_column(Text, default="[]")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")


class QuestionnaireAssignment(Base, TimestampMixin):
    __tablename__ = "questionnaire_assignments"
    __table_args__ = (UniqueConstraint("appointment_id", "questionnaire_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    questionnaire_id: Mapped[int] = mapped_column(
        ForeignKey("questionnaires.id"), index=True
    )
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="ASSIGNED")


class QuestionnaireResponse(Base, TimestampMixin):
    __tablename__ = "questionnaire_responses"
    __table_args__ = (UniqueConstraint("assignment_id", "question_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("questionnaire_assignments.id"), index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questionnaire_questions.id"), index=True
    )
    answer_json: Mapped[str] = mapped_column(Text)


class AIConversation(Base, TimestampMixin):
    __tablename__ = "ai_conversations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")


class AIContext(Base, TimestampMixin):
    __tablename__ = "ai_contexts"
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_conversations.id"), primary_key=True
    )
    current_intent: Mapped[str | None] = mapped_column(String(80))
    selected_hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"))
    selected_doctor_id: Mapped[int | None] = mapped_column(ForeignKey("doctors.id"))
    selected_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id")
    )
    state_json: Mapped[str] = mapped_column(Text, default="{}")


class CapabilityDefinition(Base, TimestampMixin):
    __tablename__ = "capability_definitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    input_schema_json: Mapped[str] = mapped_column(Text)
    output_schema_json: Mapped[str] = mapped_column(Text)
    authorization_json: Mapped[str] = mapped_column(Text)
    retry_policy_json: Mapped[str] = mapped_column(Text, default="{}")
    requires_idempotency: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_verification: Mapped[bool] = mapped_column(Boolean, default=False)


class CapabilityExecution(Base, TimestampMixin):
    __tablename__ = "capability_executions"
    id: Mapped[int] = mapped_column(primary_key=True)
    capability_name: Mapped[str] = mapped_column(String(120), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"), index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(80))


class HealthcareSystemConnection(Base, TimestampMixin):
    __tablename__ = "healthcare_system_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    connector_type: Mapped[str] = mapped_column(String(80), default="MOCK_EHR")
    base_url: Mapped[str] = mapped_column(Text)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="DISABLED")


class ExternalIdentifierMapping(Base, TimestampMixin):
    __tablename__ = "external_identifier_mappings"
    __table_args__ = (
        UniqueConstraint("connection_id", "entity_type", "internal_id"),
        UniqueConstraint("connection_id", "entity_type", "external_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("healthcare_system_connections.id"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(40))
    internal_id: Mapped[int] = mapped_column(Integer)
    external_id: Mapped[str] = mapped_column(String(160))


class IntegrationOperation(Base, TimestampMixin):
    __tablename__ = "integration_operations"
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("healthcare_system_connections.id"), index=True
    )
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"))
    operation_type: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    external_id: Mapped[str | None] = mapped_column(String(160))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_error_message: Mapped[str | None] = mapped_column(Text)


class IntegrationVerification(Base, TimestampMixin):
    __tablename__ = "integration_verifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("integration_operations.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(40))
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReconciliationRecord(Base, TimestampMixin):
    __tablename__ = "reconciliation_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("integration_operations.id"), index=True
    )
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="REQUIRED", index=True)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    trigger_event: Mapped[str] = mapped_column(String(120), index=True)
    definition_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")


class WorkflowExecution(Base, TimestampMixin):
    __tablename__ = "workflow_executions"
    __table_args__ = (UniqueConstraint("workflow_id", "event_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id"), index=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"))
    event_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    current_step: Mapped[str | None] = mapped_column(String(120))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error_message: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("idempotency_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), index=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"))
    recipient_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(30))
    notification_type: Mapped[str] = mapped_column(String(80))
    recipient: Mapped[str] = mapped_column(String(320))
    template_data_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HumanEscalation(Base, TimestampMixin):
    __tablename__ = "human_escalations"
    id: Mapped[int] = mapped_column(primary_key=True)
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"), index=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"))
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("ai_conversations.id"))
    reason_code: Mapped[str] = mapped_column(String(80))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="OPEN", index=True)


class AIEvaluation(Base, TimestampMixin):
    __tablename__ = "ai_evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("ai_conversations.id"))
    category: Mapped[str] = mapped_column(String(80), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[int | None] = mapped_column(Integer)
    details_json: Mapped[str] = mapped_column(Text, default="{}")


class IdempotencyRecord(Base, TimestampMixin):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "idempotency_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    operation: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(30), default="IN_PROGRESS")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(40))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class OperationalEvent(Base):
    __tablename__ = "operational_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    correlation_id: Mapped[str] = mapped_column(String(64), index=True)
    hospital_id: Mapped[int | None] = mapped_column(ForeignKey("hospitals.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(40))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
