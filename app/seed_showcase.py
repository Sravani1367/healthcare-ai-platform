"""Five fully-configured demo hospitals, each with its own admin and doctor account.

Additive and idempotent: never touches the accounts/hospital created by seed_demo()
in seed.py, and re-running it is a no-op once the data exists (same pattern as
seed_demo). Deliberately leaves appointments empty — booking through the live app
(AI or Find & Book) is itself part of the demo, and a fabricated appointment here
would need a matching row in the separate Mock EHR database to be genuinely
verifiable, cancelable or reschedulable.
"""

from __future__ import annotations

import json
from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    AppointmentType,
    BlockedPeriod,
    Calendar,
    Department,
    Doctor,
    HealthcareSystemConnection,
    Hospital,
    HospitalMembership,
    Patient,
    Platform,
    Questionnaire,
    QuestionnaireQuestion,
    Specialty,
    User,
    Workflow,
    WorkingHours,
    utcnow,
)
from .security import hash_password


def _get_or_create_user(db: Session, *, email: str, password: str, role: str, full_name: str) -> User:
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        return existing
    item = User(email=email, password_hash=hash_password(password), full_name=full_name, role=role)
    db.add(item)
    db.flush()
    return item


def _get_or_create_hospital(db: Session, platform: Platform, spec: dict, reviewer: User) -> Hospital:
    existing = db.scalar(select(Hospital).where(Hospital.name == spec["name"]))
    if existing:
        return existing
    hospital = Hospital(
        platform_id=platform.id,
        name=spec["name"],
        address=spec["address"],
        contact_email=spec["contact_email"],
        contact_phone=spec["contact_phone"],
        operating_hours_json=json.dumps(spec["operating_hours"]),
        supported_systems_json=json.dumps(["Mock EHR"]),
        status="APPROVED",
        submitted_at=utcnow(),
        reviewed_at=utcnow(),
        reviewed_by=reviewer.id,
        review_notes="Approved as part of platform showcase seed data.",
    )
    db.add(hospital)
    db.flush()
    return hospital


def _get_or_create_membership(db: Session, hospital: Hospital, user: User, role: str) -> None:
    existing = db.scalar(select(HospitalMembership).where(
        HospitalMembership.hospital_id == hospital.id, HospitalMembership.user_id == user.id,
    ))
    if existing is None:
        db.add(HospitalMembership(hospital_id=hospital.id, user_id=user.id, role=role))


def _get_or_create_department(db: Session, hospital: Hospital, name: str) -> Department:
    existing = db.scalar(select(Department).where(Department.hospital_id == hospital.id, Department.name == name))
    if existing:
        return existing
    item = Department(hospital_id=hospital.id, name=name)
    db.add(item)
    db.flush()
    return item


def _get_or_create_specialty(db: Session, hospital: Hospital, name: str, department: Department | None) -> Specialty:
    existing = db.scalar(select(Specialty).where(Specialty.hospital_id == hospital.id, Specialty.name == name))
    if existing:
        return existing
    item = Specialty(hospital_id=hospital.id, department_id=department.id if department else None, name=name)
    db.add(item)
    db.flush()
    return item


def _get_or_create_appointment_type(db: Session, hospital: Hospital, specialty: Specialty, name: str, duration: int) -> AppointmentType:
    existing = db.scalar(select(AppointmentType).where(AppointmentType.hospital_id == hospital.id, AppointmentType.name == name))
    if existing:
        return existing
    item = AppointmentType(hospital_id=hospital.id, specialty_id=specialty.id, name=name, duration_minutes=duration)
    db.add(item)
    db.flush()
    return item


def _get_or_create_doctor(db: Session, hospital: Hospital, user: User, spec: dict, department: Department, specialty: Specialty, reviewer: User) -> Doctor:
    existing = db.scalar(select(Doctor).where(Doctor.user_id == user.id))
    if existing:
        return existing
    doctor = Doctor(
        hospital_id=hospital.id, user_id=user.id, department_id=department.id, specialty_id=specialty.id,
        name=spec["name"], photo_url=spec.get("photo_url"), qualifications=spec["qualifications"],
        experience_years=spec["experience_years"], languages_json=json.dumps(spec["languages"]),
        consultation_types_json=json.dumps(spec["consultation_types"]),
        external_provider_id=spec["external_provider_id"], status="ACTIVE",
        reviewed_by=reviewer.id, reviewed_at=utcnow(), review_notes="Approved as part of platform showcase seed data.",
    )
    db.add(doctor)
    db.flush()
    return doctor


def _get_or_create_calendar(db: Session, hospital: Hospital, doctor: Doctor, appointment_type: AppointmentType, timezone_name: str) -> Calendar:
    existing = db.scalar(select(Calendar).where(Calendar.doctor_id == doctor.id, Calendar.appointment_type_id == appointment_type.id))
    if existing:
        return existing
    item = Calendar(hospital_id=hospital.id, doctor_id=doctor.id, appointment_type_id=appointment_type.id, timezone=timezone_name)
    db.add(item)
    db.flush()
    return item


def _ensure_working_hours(db: Session, calendar: Calendar, windows: list[tuple[int, time, time]]) -> None:
    if db.scalar(select(WorkingHours).where(WorkingHours.calendar_id == calendar.id)):
        return
    for weekday, start, end in windows:
        db.add(WorkingHours(calendar_id=calendar.id, weekday=weekday, start_time=start, end_time=end))


def _ensure_blocked_period(db: Session, calendar: Calendar, *, days_from_now: int, hour_start: int, hour_end: int, reason: str, kind: str) -> None:
    if db.scalar(select(BlockedPeriod).where(BlockedPeriod.calendar_id == calendar.id)):
        return
    day = date.today() + timedelta(days=days_from_now)
    db.add(BlockedPeriod(
        calendar_id=calendar.id,
        starts_at=utcnow().replace(year=day.year, month=day.month, day=day.day, hour=hour_start, minute=0, second=0, microsecond=0),
        ends_at=utcnow().replace(year=day.year, month=day.month, day=day.day, hour=hour_end, minute=0, second=0, microsecond=0),
        reason=reason, kind=kind,
    ))


def _ensure_connection(db: Session, hospital: Hospital) -> None:
    if not db.scalar(select(HealthcareSystemConnection).where(HealthcareSystemConnection.hospital_id == hospital.id)):
        db.add(HealthcareSystemConnection(hospital_id=hospital.id, connector_type="MOCK_EHR", base_url=settings.mock_ehr_url, status="ACTIVE"))


def _ensure_questionnaire(db: Session, hospital: Hospital, publisher: User, spec: dict) -> None:
    existing = db.scalar(select(Questionnaire).where(Questionnaire.hospital_id == hospital.id, Questionnaire.title == spec["title"]))
    if existing:
        return
    item = Questionnaire(
        hospital_id=hospital.id, specialty_id=spec.get("specialty_id"), doctor_id=spec.get("doctor_id"),
        appointment_type_id=spec.get("appointment_type_id"), title=spec["title"],
        approved_category=spec["approved_category"], status="ACTIVE",
        published_by_user_id=publisher.id, published_at=utcnow(),
    )
    db.add(item)
    db.flush()
    for question in spec["questions"]:
        position, prompt, question_type, required, options = question[:5]
        validation = question[5] if len(question) > 5 else {}
        db.add(QuestionnaireQuestion(
            questionnaire_id=item.id, position=position, prompt=prompt, question_type=question_type,
            required=required, options_json=json.dumps(options), validation_json=json.dumps(validation),
        ))


def _ensure_workflow(db: Session, hospital: Hospital, name: str, trigger_event: str, definition: dict) -> None:
    if not db.scalar(select(Workflow).where(Workflow.hospital_id == hospital.id, Workflow.name == name)):
        db.add(Workflow(hospital_id=hospital.id, name=name, trigger_event=trigger_event, definition_json=json.dumps(definition)))


def _ensure_patient(db: Session, user: User, spec: dict) -> Patient:
    existing = db.scalar(select(Patient).where(Patient.user_id == user.id))
    if existing:
        return existing
    item = Patient(
        user_id=user.id, name=spec["name"], email=user.email, phone=spec.get("phone"),
        date_of_birth=spec.get("date_of_birth"), communication_preference=spec.get("communication_preference", "EMAIL"),
    )
    db.add(item)
    db.flush()
    return item


HOSPITALS = [
    {
        "key": "riverside",
        "name": "Riverside Heart & Vascular Institute",
        "address": "88 Riverside Boulevard, Portland",
        "contact_email": "frontdesk@riverside-hvi.example",
        "contact_phone": "+1-555-0201",
        "operating_hours": {"weekdays": "08:00-18:00", "saturday": "09:00-13:00"},
        "timezone": "America/Los_Angeles",
        "admin": {"email": "admin@riverside-hvi.example", "password": "RiversideAdmin!26", "full_name": "Priya Chandran"},
        "departments": ["Cardiology", "Vascular Surgery"],
        "specialties": [("Cardiology", "Cardiology"), ("Vascular Surgery", "Vascular Surgery")],
        "doctor": {
            "email": "d.okafor@riverside-hvi.example", "password": "RiversideDoctor!26",
            "name": "Dr. Ijeoma Okafor", "department": "Cardiology", "specialty": "Cardiology",
            "qualifications": "MBBS, MD Cardiology, Fellowship in Interventional Cardiology",
            "experience_years": 16, "languages": ["English", "Igbo", "French"],
            "consultation_types": ["In person", "Video"], "external_provider_id": "PROV-OKAFOR-201",
            "photo_url": None,
        },
        "appointment_types": [("Cardiology consultation", 30), ("Echocardiogram review", 45)],
        "working_hours": [(0, time(8, 0), time(16, 0)), (1, time(8, 0), time(16, 0)), (2, time(8, 0), time(16, 0)), (3, time(8, 0), time(16, 0)), (4, time(8, 0), time(14, 0))],
        "blocked_period": {"days_from_now": 9, "hour_start": 8, "hour_end": 16, "reason": "Cardiology conference", "kind": "LEAVE"},
        "questionnaires": [
            {
                "title": "Cardiology pre-visit intake", "approved_category": "PRE_VISIT_ADMIN",
                "scope": "specialty",
                "questions": [
                    (1, "Have you experienced chest discomfort in the last 7 days?", "YES_NO", True, []),
                    (2, "Which symptom brings you in today?", "CHOICE", True, ["Palpitations", "Shortness of breath", "Fatigue", "Routine follow-up"]),
                    (3, "Select any relevant history (choose all that apply).", "MULTIPLE_CHOICE", False, ["Hypertension", "Diabetes", "High cholesterol", "Family history of heart disease"]),
                    (4, "Approximate resting heart rate today, if known (bpm).", "NUMERIC", False, []),
                    (5, "Date of your most recent ECG, if any.", "DATE", False, []),
                    (6, "Anything else the doctor should know before your visit?", "LONG_TEXT", False, []),
                ],
            },
        ],
        "workflow_reminder_delay": 24 * 3600,
    },
    {
        "key": "sunrise",
        "name": "Sunrise Children's & Family Clinic",
        "address": "14 Meadow Lane, Austin",
        "contact_email": "hello@sunrise-family.example",
        "contact_phone": "+1-555-0202",
        "operating_hours": {"weekdays": "07:30-17:00"},
        "timezone": "America/Chicago",
        "admin": {"email": "admin@sunrise-family.example", "password": "SunriseAdmin!26", "full_name": "Marcus Webb"},
        "departments": ["Pediatrics"],
        "specialties": [("Pediatrics", "Pediatrics"), ("Pediatric Dermatology", "Pediatrics")],
        "doctor": {
            "email": "d.tanaka@sunrise-family.example", "password": "SunriseDoctor!26",
            "name": "Dr. Naomi Tanaka", "department": "Pediatrics", "specialty": "Pediatrics",
            "qualifications": "MBBS, MD Pediatrics, Board Certified in Pediatric Care",
            "experience_years": 9, "languages": ["English", "Japanese", "Spanish"],
            "consultation_types": ["In person", "Phone"], "external_provider_id": "PROV-TANAKA-202",
            "photo_url": None,
        },
        "appointment_types": [("Well-child visit", 20), ("Sick visit", 15), ("Vaccination", 15)],
        "working_hours": [(0, time(7, 30), time(15, 30)), (1, time(7, 30), time(15, 30)), (2, time(7, 30), time(15, 30)), (3, time(7, 30), time(15, 30)), (4, time(7, 30), time(15, 30)), (5, time(9, 0), time(12, 0))],
        "blocked_period": {"days_from_now": 5, "hour_start": 12, "hour_end": 13, "reason": "Staff training session", "kind": "BLOCK"},
        "questionnaires": [
            {
                "title": "Pediatric visit intake", "approved_category": "PRE_VISIT_ADMIN",
                "scope": "doctor",
                "questions": [
                    (1, "Is this visit for a new or ongoing concern?", "CHOICE", True, ["New concern", "Ongoing concern", "Routine check-up"]),
                    (2, "Has the child had a fever in the last 48 hours?", "YES_NO", True, []),
                    (3, "Child's current weight, if known (kg).", "NUMERIC", False, []),
                    (4, "List any known allergies.", "SHORT_TEXT", False, []),
                    (5, "Preferred appointment reminder time.", "STRUCTURED", False, []),
                ],
            },
        ],
        "workflow_reminder_delay": 12 * 3600,
    },
    {
        "key": "summit",
        "name": "Summit Orthopedic & Sports Medicine",
        "address": "500 Alpine Ridge Road, Denver",
        "contact_email": "care@summit-ortho.example",
        "contact_phone": "+1-555-0203",
        "operating_hours": {"weekdays": "06:00-19:00"},
        "timezone": "America/Denver",
        "admin": {"email": "admin@summit-ortho.example", "password": "SummitAdmin!26", "full_name": "Dana Whitfield"},
        "departments": ["Orthopedics", "Physical Therapy"],
        "specialties": [("Sports Medicine", "Orthopedics"), ("Joint Replacement", "Orthopedics")],
        "doctor": {
            "email": "d.reyes@summit-ortho.example", "password": "SummitDoctor!26",
            "name": "Dr. Camila Reyes", "department": "Orthopedics", "specialty": "Sports Medicine",
            "qualifications": "MBBS, MS Orthopedics, Fellowship in Sports Medicine",
            "experience_years": 11, "languages": ["English", "Spanish", "Portuguese"],
            "consultation_types": ["In person", "Video"], "external_provider_id": "PROV-REYES-203",
            "photo_url": None,
        },
        "appointment_types": [("Sports injury consultation", 30), ("Post-op follow-up", 20), ("MRI results review", 30)],
        "working_hours": [(0, time(6, 0), time(14, 0)), (1, time(6, 0), time(14, 0)), (2, time(10, 0), time(18, 0)), (3, time(10, 0), time(18, 0)), (4, time(6, 0), time(14, 0))],
        "blocked_period": {"days_from_now": 3, "hour_start": 6, "hour_end": 14, "reason": "On-call at regional marathon medical tent", "kind": "BLOCK"},
        "questionnaires": [
            {
                "title": "Sports medicine intake", "approved_category": "PRE_VISIT_ADMIN",
                "scope": "appointment_type", "appointment_type_name": "Sports injury consultation",
                "questions": [
                    (1, "When did the injury occur?", "DATE", True, []),
                    (2, "Rate your current pain level (0-10).", "NUMERIC", True, [], {"min": 0, "max": 10}),
                    (3, "Which area is affected?", "CHOICE", True, ["Knee", "Shoulder", "Ankle", "Back", "Other"]),
                    (4, "Have you had imaging done for this injury?", "YES_NO", False, []),
                    (5, "Describe how the injury happened.", "LONG_TEXT", False, []),
                ],
            },
        ],
        "workflow_reminder_delay": 6 * 3600,
    },
    {
        "key": "harbor",
        "name": "Harbor View Women's Health Center",
        "address": "27 Lighthouse Way, Boston",
        "contact_email": "appointments@harborview-womens.example",
        "contact_phone": "+1-555-0204",
        "operating_hours": {"weekdays": "08:00-17:30"},
        "timezone": "America/New_York",
        "admin": {"email": "admin@harborview-womens.example", "password": "HarborAdmin!26", "full_name": "Eleanor Voss"},
        "departments": ["Obstetrics & Gynecology"],
        "specialties": [("Obstetrics", "Obstetrics & Gynecology"), ("Gynecology", "Obstetrics & Gynecology")],
        "doctor": {
            "email": "d.singh@harborview-womens.example", "password": "HarborDoctor!26",
            "name": "Dr. Amrita Singh", "department": "Obstetrics & Gynecology", "specialty": "Obstetrics",
            "qualifications": "MBBS, MD Obstetrics & Gynecology",
            "experience_years": 14, "languages": ["English", "Hindi", "Punjabi"],
            "consultation_types": ["In person"], "external_provider_id": "PROV-SINGH-204",
            "photo_url": None,
        },
        "appointment_types": [("Prenatal check-up", 30), ("Annual wellness exam", 30), ("New patient consultation", 45)],
        "working_hours": [(0, time(8, 0), time(16, 0)), (1, time(8, 0), time(16, 0)), (2, time(8, 0), time(16, 0)), (3, time(8, 0), time(16, 0))],
        "blocked_period": {"days_from_now": 7, "hour_start": 8, "hour_end": 16, "reason": "Approved leave", "kind": "LEAVE"},
        "questionnaires": [
            {
                "title": "Women's health intake", "approved_category": "PRE_VISIT_ADMIN",
                "scope": "hospital",
                "questions": [
                    (1, "Is this a prenatal visit?", "YES_NO", True, []),
                    (2, "Date of last menstrual period, if applicable.", "DATE", False, []),
                    (3, "Any new symptoms since your last visit?", "SHORT_TEXT", False, []),
                    (4, "Preferred pharmacy for prescriptions, if any.", "SHORT_TEXT", False, []),
                ],
            },
        ],
        "workflow_reminder_delay": 24 * 3600,
    },
    {
        "key": "northgate",
        "name": "Northgate Behavioral & Mental Health",
        "address": "310 Aspen Court, Seattle",
        "contact_email": "intake@northgate-bh.example",
        "contact_phone": "+1-555-0205",
        "operating_hours": {"weekdays": "09:00-20:00"},
        "timezone": "America/Los_Angeles",
        "admin": {"email": "admin@northgate-bh.example", "password": "NorthgateAdmin!26", "full_name": "Jordan Ferris"},
        "departments": ["Behavioral Health"],
        "specialties": [("Psychiatry", "Behavioral Health"), ("Counseling", "Behavioral Health")],
        "doctor": {
            "email": "d.almeida@northgate-bh.example", "password": "NorthgateDoctor!26",
            "name": "Dr. Rafael Almeida", "department": "Behavioral Health", "specialty": "Psychiatry",
            "qualifications": "MBBS, MD Psychiatry",
            "experience_years": 8, "languages": ["English", "Portuguese"],
            "consultation_types": ["Video", "In person"], "external_provider_id": "PROV-ALMEIDA-205",
            "photo_url": None,
        },
        "appointment_types": [("Initial psychiatric evaluation", 60), ("Medication management follow-up", 20), ("Telehealth check-in", 30)],
        "working_hours": [(0, time(9, 0), time(17, 0)), (1, time(9, 0), time(17, 0)), (2, time(9, 0), time(17, 0)), (3, time(12, 0), time(20, 0)), (4, time(9, 0), time(17, 0))],
        "blocked_period": {"days_from_now": 4, "hour_start": 9, "hour_end": 12, "reason": "Clinical supervision block", "kind": "BLOCK"},
        "questionnaires": [
            {
                "title": "Behavioral health intake", "approved_category": "PRE_VISIT_ADMIN",
                "scope": "specialty",
                "questions": [
                    (1, "Is this your first psychiatric evaluation?", "YES_NO", True, []),
                    (2, "How would you describe your primary concern today?", "LONG_TEXT", True, []),
                    (3, "Current medications, if any.", "SHORT_TEXT", False, []),
                    (4, "On a scale of 0-10, how would you rate your stress level this week?", "NUMERIC", False, [], {"min": 0, "max": 10}),
                    (5, "Preferred visit format.", "CHOICE", False, ["Video", "In person"]),
                ],
            },
        ],
        "workflow_reminder_delay": 24 * 3600,
    },
]


def seed_showcase_hospitals(db: Session) -> None:
    if not settings.allow_demo_seed:
        return
    platform = db.scalar(select(Platform).where(Platform.name == "Healthcare AI Platform"))
    if platform is None:
        return
    platform_admin = db.scalar(select(User).where(User.email == "platform.admin@example.com"))
    if platform_admin is None:
        return

    for spec in HOSPITALS:
        hospital = _get_or_create_hospital(db, platform, spec, platform_admin)

        admin_user = _get_or_create_user(
            db, email=spec["admin"]["email"], password=spec["admin"]["password"],
            role="HOSPITAL_ADMIN", full_name=spec["admin"]["full_name"],
        )
        _get_or_create_membership(db, hospital, admin_user, "HOSPITAL_ADMIN")

        departments = {name: _get_or_create_department(db, hospital, name) for name in spec["departments"]}
        specialties = {
            spec_name: _get_or_create_specialty(db, hospital, spec_name, departments.get(dept_name))
            for spec_name, dept_name in spec["specialties"]
        }

        doctor_spec = spec["doctor"]
        doctor_user = _get_or_create_user(
            db, email=doctor_spec["email"], password=doctor_spec["password"],
            role="DOCTOR", full_name=doctor_spec["name"],
        )
        _get_or_create_membership(db, hospital, doctor_user, "DOCTOR")
        doctor_department = departments[doctor_spec["department"]]
        doctor_specialty = specialties[doctor_spec["specialty"]]
        doctor = _get_or_create_doctor(db, hospital, doctor_user, doctor_spec, doctor_department, doctor_specialty, platform_admin)

        appointment_types = {
            name: _get_or_create_appointment_type(db, hospital, doctor_specialty, name, duration)
            for name, duration in spec["appointment_types"]
        }
        primary_type = appointment_types[spec["appointment_types"][0][0]]
        calendar = _get_or_create_calendar(db, hospital, doctor, primary_type, spec["timezone"])
        _ensure_working_hours(db, calendar, spec["working_hours"])
        block = spec["blocked_period"]
        _ensure_blocked_period(
            db, calendar, days_from_now=block["days_from_now"], hour_start=block["hour_start"],
            hour_end=block["hour_end"], reason=block["reason"], kind=block["kind"],
        )

        # A second calendar (different appointment type) if one exists, so the doctor
        # dashboard and hospital admin's calendar list show more than one entry.
        if len(spec["appointment_types"]) > 1:
            second_type = appointment_types[spec["appointment_types"][1][0]]
            second_calendar = _get_or_create_calendar(db, hospital, doctor, second_type, spec["timezone"])
            _ensure_working_hours(db, second_calendar, spec["working_hours"])

        _ensure_connection(db, hospital)

        for questionnaire_spec in spec["questionnaires"]:
            resolved = dict(questionnaire_spec)
            scope = resolved.pop("scope")
            if scope == "specialty":
                resolved["specialty_id"] = doctor_specialty.id
            elif scope == "doctor":
                resolved["doctor_id"] = doctor.id
            elif scope == "appointment_type":
                type_name = resolved.pop("appointment_type_name")
                resolved["appointment_type_id"] = appointment_types[type_name].id
            # scope == "hospital": no extra selector fields, applies hospital-wide.
            _ensure_questionnaire(db, hospital, admin_user, resolved)

        _ensure_workflow(
            db, hospital, "Confirmed appointment follow-up", "APPOINTMENT_CONFIRMED",
            {"steps": ["ASSIGN_QUESTIONNAIRE", "SEND_CONFIRMATION_EMAIL"], "max_attempts": 3},
        )
        _ensure_workflow(
            db, hospital, "Pre-visit reminder", "APPOINTMENT_CONFIRMED",
            {"steps": ["SEND_REMINDER_EMAIL"], "delay_seconds": spec["workflow_reminder_delay"], "max_attempts": 3},
        )

    # One patient per showcase hospital's region, so tenant-isolation and
    # cross-hospital "own appointments across hospitals" scenarios are demonstrable.
    patients = [
        {"email": "olivia.martin@example.com", "password": "PatientDemo!26", "full_name": "Olivia Martin", "phone": "+1-555-0301", "dob": date(1990, 4, 12)},
        {"email": "liam.chen@example.com", "password": "PatientDemo!26", "full_name": "Liam Chen", "phone": "+1-555-0302", "dob": date(1985, 11, 3)},
        {"email": "sofia.rossi@example.com", "password": "PatientDemo!26", "full_name": "Sofia Rossi", "phone": "+1-555-0303", "dob": date(1998, 7, 21)},
    ]
    for patient_spec in patients:
        patient_user = _get_or_create_user(
            db, email=patient_spec["email"], password=patient_spec["password"],
            role="PATIENT", full_name=patient_spec["full_name"],
        )
        _ensure_patient(db, patient_user, {
            "name": patient_spec["full_name"], "phone": patient_spec["phone"],
            "date_of_birth": patient_spec["dob"], "communication_preference": "EMAIL",
        })

    db.commit()
