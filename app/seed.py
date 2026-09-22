from __future__ import annotations

import json
from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    AppointmentType, Calendar, CapabilityDefinition, Department, Doctor,
    HealthcareSystemConnection, Hospital, HospitalMembership, Patient, Platform,
    Questionnaire, QuestionnaireQuestion, Specialty, User, Workflow, WorkingHours,
)
from .security import hash_password


DEMO_ACCOUNTS = {
    "platform_admin": ("platform.admin@example.com", "DemoAdmin!2026", "PLATFORM_ADMIN"),
    "hospital_admin": ("hospital.admin@example.com", "DemoHospital!2026", "HOSPITAL_ADMIN"),
    "doctor": ("doctor@example.com", "DemoDoctor!2026", "DOCTOR"),
    "patient": ("patient@example.com", "DemoPatient!2026", "PATIENT"),
}


def _user(db: Session, key: str, name: str) -> User:
    email, password, role = DEMO_ACCOUNTS[key]
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        return existing
    item = User(email=email, password_hash=hash_password(password), full_name=name, role=role)
    db.add(item); db.flush()
    return item


def seed_demo(db: Session) -> None:
    if not settings.allow_demo_seed:
        return
    platform = db.scalar(select(Platform).where(Platform.name == "Healthcare AI Platform"))
    if platform is None:
        platform = Platform(name="Healthcare AI Platform"); db.add(platform); db.flush()
    platform_admin = _user(db, "platform_admin", "Platform Administrator")
    hospital_admin = _user(db, "hospital_admin", "Hospital Administrator")
    doctor_user = _user(db, "doctor", "Dr. Alice Kumar")
    patient_user = _user(db, "patient", "Jordan Patient")

    hospital = db.scalar(select(Hospital).where(Hospital.name == "City General Hospital"))
    if hospital is None:
        hospital = Hospital(
            platform_id=platform.id, name="City General Hospital",
            address="123 Main Street, Springfield",
            contact_email="appointments@citygeneral.example", contact_phone="+1-555-0100",
            operating_hours_json=json.dumps({"weekdays": "09:00-17:00"}),
            supported_systems_json=json.dumps(["Mock EHR"]), status="APPROVED",
            reviewed_by=platform_admin.id,
        )
        db.add(hospital); db.flush()
    for user, role in [(hospital_admin, "HOSPITAL_ADMIN"), (doctor_user, "DOCTOR")]:
        if not db.scalar(select(HospitalMembership).where(
            HospitalMembership.hospital_id == hospital.id,
            HospitalMembership.user_id == user.id,
        )):
            db.add(HospitalMembership(hospital_id=hospital.id, user_id=user.id, role=role))

    department = db.scalar(select(Department).where(
        Department.hospital_id == hospital.id, Department.name == "Orthopedics"
    ))
    if department is None:
        department = Department(hospital_id=hospital.id, name="Orthopedics"); db.add(department); db.flush()
    specialty = db.scalar(select(Specialty).where(
        Specialty.hospital_id == hospital.id, Specialty.name == "Orthopedics"
    ))
    if specialty is None:
        specialty = Specialty(hospital_id=hospital.id, department_id=department.id, name="Orthopedics"); db.add(specialty); db.flush()
    doctor = db.scalar(select(Doctor).where(Doctor.user_id == doctor_user.id))
    if doctor is None:
        doctor = Doctor(
            hospital_id=hospital.id, user_id=doctor_user.id, department_id=department.id,
            specialty_id=specialty.id, name="Dr. Alice Kumar",
            qualifications="MBBS, MS Orthopedics", experience_years=12,
            languages_json=json.dumps(["English", "Hindi"]),
            consultation_types_json=json.dumps(["In person"]),
            external_provider_id="PROV-ALICE-001", status="ACTIVE",
        )
        db.add(doctor); db.flush()
    kind = db.scalar(select(AppointmentType).where(
        AppointmentType.hospital_id == hospital.id,
        AppointmentType.name == "Orthopedic consultation",
    ))
    if kind is None:
        kind = AppointmentType(hospital_id=hospital.id, specialty_id=specialty.id, name="Orthopedic consultation", duration_minutes=30)
        db.add(kind); db.flush()
    calendar = db.scalar(select(Calendar).where(
        Calendar.doctor_id == doctor.id, Calendar.appointment_type_id == kind.id,
    ))
    if calendar is None:
        calendar = Calendar(hospital_id=hospital.id, doctor_id=doctor.id, appointment_type_id=kind.id, timezone="Asia/Kolkata")
        db.add(calendar); db.flush()
    if not db.scalar(select(WorkingHours).where(WorkingHours.calendar_id == calendar.id)):
        for weekday in range(0, 6):
            db.add(WorkingHours(calendar_id=calendar.id, weekday=weekday, start_time=time(9), end_time=time(17)))
    if not db.scalar(select(Patient).where(Patient.user_id == patient_user.id)):
        db.add(Patient(user_id=patient_user.id, name="Jordan Patient", email=patient_user.email, communication_preference="EMAIL"))
    if not db.scalar(select(HealthcareSystemConnection).where(HealthcareSystemConnection.hospital_id == hospital.id)):
        db.add(HealthcareSystemConnection(hospital_id=hospital.id, connector_type="MOCK_EHR", base_url=settings.mock_ehr_url, status="ACTIVE"))
    questionnaire = db.scalar(select(Questionnaire).where(
        Questionnaire.hospital_id == hospital.id, Questionnaire.title == "Orthopedic pre-visit",
    ))
    if questionnaire is None:
        questionnaire = Questionnaire(
            hospital_id=hospital.id, specialty_id=specialty.id,
            appointment_type_id=kind.id, title="Orthopedic pre-visit",
            approved_category="PRE_VISIT_ADMIN", status="ACTIVE",
        )
        db.add(questionnaire); db.flush()
        questions = [
            (1, "Is this your first visit for this concern?", "YES_NO", True, []),
            (2, "Which area should the doctor review?", "CHOICE", True, ["Shoulder", "Knee", "Back", "Other"]),
            (3, "Add any administrative accessibility needs.", "SHORT_TEXT", False, []),
        ]
        for position, prompt, kind_name, required, options in questions:
            db.add(QuestionnaireQuestion(
                questionnaire_id=questionnaire.id, position=position, prompt=prompt,
                question_type=kind_name, required=required, options_json=json.dumps(options),
            ))
    if not db.scalar(select(Workflow).where(
        Workflow.hospital_id == hospital.id,
        Workflow.trigger_event == "APPOINTMENT_CONFIRMED",
    )):
        db.add(Workflow(
            hospital_id=hospital.id, name="Confirmed appointment follow-up",
            trigger_event="APPOINTMENT_CONFIRMED",
            definition_json=json.dumps({"steps": ["ASSIGN_QUESTIONNAIRE", "SEND_CONFIRMATION_EMAIL"], "max_attempts": 3}),
        ))
    names = [
        "search_hospitals", "search_doctors", "check_availability", "lookup_patient",
        "get_appointment", "create_appointment", "reschedule_appointment",
        "cancel_appointment", "get_questionnaire", "submit_questionnaire",
        "send_notification", "start_workflow", "get_context", "update_preferences",
        "verify_external_appointment", "synchronize_state", "transfer_to_human",
    ]
    for name in names:
        if not db.scalar(select(CapabilityDefinition).where(CapabilityDefinition.name == name)):
            db.add(CapabilityDefinition(
                name=name, input_schema_json="{}", output_schema_json="{}",
                authorization_json=json.dumps({"enforced": True}),
                requires_idempotency=name in {"create_appointment", "reschedule_appointment", "cancel_appointment", "send_notification", "start_workflow"},
                requires_verification=name in {"create_appointment", "reschedule_appointment", "cancel_appointment"},
            ))
    db.commit()

