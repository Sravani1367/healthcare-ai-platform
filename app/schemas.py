from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    full_name: str = Field(min_length=2, max_length=160)
    role: Literal["PATIENT", "HOSPITAL_ADMIN", "DOCTOR"] = "PATIENT"


class LoginRequest(StrictModel):
    email: EmailStr
    password: str


class HospitalCreateRequest(StrictModel):
    name: str = Field(min_length=2, max_length=200)
    address: str = Field(min_length=4, max_length=1000)
    contact_email: EmailStr
    contact_phone: str | None = Field(default=None, max_length=40)
    operating_hours: dict[str, Any] = Field(default_factory=dict)
    supported_systems: list[str] = Field(default_factory=list)


class HospitalReviewRequest(StrictModel):
    decision: Literal["APPROVED", "REJECTED", "CORRECTIONS_REQUIRED"]
    notes: str | None = Field(default=None, max_length=2000)


class DepartmentCreateRequest(StrictModel):
    name: str = Field(min_length=2, max_length=160)


class SpecialtyCreateRequest(StrictModel):
    name: str = Field(min_length=2, max_length=160)
    department_id: int | None = None


class DoctorCreateRequest(StrictModel):
    name: str = Field(min_length=2, max_length=160)
    specialty_id: int
    department_id: int | None = None
    user_email: EmailStr | None = None
    photo_url: str | None = None
    qualifications: str | None = Field(default=None, max_length=2000)
    experience_years: int | None = Field(default=None, ge=0, le=80)
    languages: list[str] = Field(default_factory=list)
    consultation_types: list[str] = Field(default_factory=list)
    external_provider_id: str | None = Field(default=None, max_length=160)


class DoctorReviewRequest(StrictModel):
    decision: Literal["APPROVED", "REJECTED"]
    notes: str | None = Field(default=None, max_length=1000)


class AppointmentTypeCreateRequest(StrictModel):
    name: str = Field(min_length=2, max_length=160)
    specialty_id: int | None = None
    duration_minutes: int = Field(default=30, ge=5, le=480)


class CalendarCreateRequest(StrictModel):
    doctor_id: int
    appointment_type_id: int
    timezone: str = Field(default="UTC", max_length=80)


class WorkingHoursCreateRequest(StrictModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def valid_interval(self):
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class BlockedPeriodCreateRequest(StrictModel):
    starts_at: datetime
    ends_at: datetime
    reason: str | None = Field(default=None, max_length=240)
    kind: Literal["BLOCK", "LEAVE"] = "BLOCK"

    @model_validator(mode="after")
    def valid_interval(self):
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self


class PatientProfileUpdateRequest(StrictModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    date_of_birth: date | None = None
    communication_preference: Literal["EMAIL", "SMS", "VOICE"] | None = None


class PreferenceRequest(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    value: Any


class AvailabilityQuery(StrictModel):
    doctor_id: int
    appointment_type_id: int
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def valid_range(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        if (self.date_to - self.date_from).days > 31:
            raise ValueError("date range cannot exceed 31 days")
        return self


class AppointmentCreateRequest(StrictModel):
    doctor_id: int
    appointment_type_id: int
    starts_at: datetime
    idempotency_key: str = Field(min_length=8, max_length=160)
    slot_quote_id: str = Field(min_length=1, max_length=4000)
    confirmation_token: str = Field(min_length=1, max_length=4000)


class AppointmentRescheduleRequest(StrictModel):
    starts_at: datetime
    idempotency_key: str = Field(min_length=8, max_length=160)
    expected_version: int = Field(ge=1)
    slot_quote_id: str = Field(min_length=1, max_length=4000)
    confirmation_token: str = Field(min_length=1, max_length=4000)


class AppointmentCancelRequest(StrictModel):
    idempotency_key: str = Field(min_length=8, max_length=160)
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=500)


QuestionType = Literal[
    "YES_NO", "CHOICE", "MULTIPLE_CHOICE", "NUMERIC", "DATE",
    "SHORT_TEXT", "LONG_TEXT", "STRUCTURED",
]


class QuestionCreateRequest(StrictModel):
    position: int = Field(ge=1)
    prompt: str = Field(min_length=2, max_length=1000)
    question_type: QuestionType
    required: bool = False
    options: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)


class QuestionnaireCreateRequest(StrictModel):
    title: str = Field(min_length=2, max_length=200)
    specialty_id: int | None = None
    doctor_id: int | None = None
    appointment_type_id: int | None = None
    approved_category: str | None = Field(default=None, max_length=120)
    questions: list[QuestionCreateRequest] = Field(min_length=1)
    status: Literal["DRAFT", "ACTIVE"] = "DRAFT"


class QuestionnaireProposeRequest(StrictModel):
    title: str = Field(min_length=2, max_length=200)
    specialty_id: int | None = None
    appointment_type_id: int | None = None
    approved_category: str | None = Field(default=None, max_length=120)
    questions: list[QuestionCreateRequest] = Field(min_length=1)


class QuestionnaireAnswersRequest(StrictModel):
    answers: dict[int, Any]


class HealthcareConnectionRequest(StrictModel):
    connector_type: Literal["MOCK_EHR"] = "MOCK_EHR"
    base_url: str
    config: dict[str, Any] = Field(default_factory=dict)
    status: Literal["DISABLED", "ACTIVE"] = "ACTIVE"


class WorkflowCreateRequest(StrictModel):
    name: str = Field(min_length=2, max_length=160)
    trigger_event: str = Field(min_length=2, max_length=120)
    definition: dict[str, Any] = Field(default_factory=dict)


class ReconciliationResolveRequest(StrictModel):
    resolution: str = Field(min_length=2, max_length=2000)
    final_state: Literal["CONFIRMED", "FAILED", "CANCELLED"]


class AgentMessageRequest(StrictModel):
    conversation_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


class VoiceTurnRequest(StrictModel):
    conversation_id: str | None = Field(default=None, max_length=64)
    transcript: str = Field(min_length=1, max_length=4000)


class TelephoneEventRequest(StrictModel):
    call_id: str = Field(min_length=1, max_length=160)
    event: Literal["STARTED", "TRANSCRIPT", "FAILED", "ENDED"]
    patient_email: EmailStr | None = None
    transcript: str | None = Field(default=None, max_length=4000)
    signature: str | None = None


class EvaluationRequest(StrictModel):
    conversation_id: str | None = None
    category: str = Field(min_length=2, max_length=80)
    passed: bool
    score: int | None = Field(default=None, ge=0, le=100)
    details: dict[str, Any] = Field(default_factory=dict)

