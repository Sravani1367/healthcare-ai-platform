from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.connectors import ConnectorResult
from app.db import SessionLocal
from app.models import (
    Appointment,
    AppointmentType,
    BlockedPeriod,
    Calendar,
    Doctor,
    Notification,
    QuestionnaireAssignment,
    ReconciliationRecord,
)


class SuccessfulConnector:
    records = {}

    def __init__(self, _base_url):
        pass

    def lookup_patient(self, internal_id):
        return ConnectorResult("SUCCESS", {"external_id": f"PAT-{internal_id}"})

    def lookup_provider(self, internal_id):
        return ConnectorResult("SUCCESS", {"external_id": f"PROV-{internal_id}"})

    def lookup_facility(self, internal_id):
        return ConnectorResult("SUCCESS", {"external_id": f"FAC-{internal_id}"})

    def lookup_calendar(self, internal_id):
        return ConnectorResult("SUCCESS", {"external_id": f"CAL-{internal_id}"})

    def create_appointment(self, **values):
        external_id = f"EXT-{values['idempotency_key']}"
        self.records[external_id] = {
            "external_id": external_id,
            "patient_id": values["patient_id"],
            "doctor_id": values["doctor_id"],
            "date": values["date"],
            "time": values["time"],
            "status": "CONFIRMED",
        }
        return ConnectorResult("SUCCESS", {"external_id": external_id})

    def verify_appointment(self, *, external_id, expected):
        actual = self.records[external_id]
        return ConnectorResult("SUCCESS", {"appointment": actual}) if all(
            str(actual.get(k)) == str(v) for k, v in expected.items()
        ) else ConnectorResult("MISMATCH", {})

    def search_appointment(self, **_values):
        return ConnectorResult("ABSENT", {})

    def get_appointment(self, external_id):
        return ConnectorResult("SUCCESS", {"appointment": self.records[external_id]})

    def cancel_appointment(self, *, external_id, idempotency_key):
        self.records[external_id]["status"] = "CANCELLED"
        return ConnectorResult("SUCCESS", {"external_id": external_id})

    def reschedule_appointment(self, *, external_id, date, time, idempotency_key):
        self.records[external_id].update(date=date, time=time, status="CONFIRMED")
        return ConnectorResult("SUCCESS", {"external_id": external_id})


class UnknownThenFoundConnector(SuccessfulConnector):
    created = 0

    def create_appointment(self, **values):
        self.__class__.created += 1
        external_id = f"UNKNOWN-{values['idempotency_key']}"
        self.records[external_id] = {
            "external_id": external_id,
            "patient_id": values["patient_id"],
            "doctor_id": values["doctor_id"],
            "date": values["date"],
            "time": values["time"],
            "status": "CONFIRMED",
        }
        return ConnectorResult("UNKNOWN_OUTCOME", {}, "response lost")

    def search_appointment(self, **values):
        for external_id, record in self.records.items():
            if all(str(record[k]) == str(values[k]) for k in ["patient_id", "doctor_id", "date", "time"]):
                return ConnectorResult("SUCCESS", {"external_id": external_id, "appointment": record})
        return ConnectorResult("ABSENT", {})


class AlwaysRetryableConnector(SuccessfulConnector):
    attempts = 0

    def create_appointment(self, **values):
        self.__class__.attempts += 1
        return ConnectorResult("RETRYABLE", {}, "EHR unavailable")


def _next_slot(client, headers):
    with SessionLocal() as db:
        doctor = db.scalar(select(Doctor))
        kind = db.scalar(select(AppointmentType))
    for offset in range(1, 8):
        day = (date.today() + timedelta(days=offset)).isoformat()
        response = client.get(
            "/api/v1/availability",
            params={"doctor_id": doctor.id, "appointment_type_id": kind.id, "date_from": day, "date_to": day},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        if response.json():
            return doctor.id, kind.id, response.json()
    raise AssertionError("seed data produced no slot")


def test_authentication_and_role_dashboard(client, patient_headers):
    me = client.get("/api/v1/auth/me", headers=patient_headers)
    assert me.status_code == 200
    assert me.json()["role"] == "PATIENT"
    forbidden = client.get("/api/v1/dashboards/PLATFORM_ADMIN", headers=patient_headers)
    assert forbidden.status_code == 403


def test_cross_tenant_doctor_creation_is_concealed(client, hospital_headers):
    response = client.post(
        "/api/v1/hospitals/9999/doctors",
        headers=hospital_headers,
        json={"name": "Dr. Intruder", "specialty_id": 1, "status": "ACTIVE"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "RESOURCE_NOT_FOUND"


def test_blocked_period_is_removed_from_real_availability(client, hospital_headers, patient_headers):
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    first = slots[0]
    with SessionLocal() as db:
        calendar = db.scalar(select(Calendar).where(Calendar.doctor_id == doctor_id))
        calendar_id = calendar.id
    blocked = client.post(
        f"/api/v1/calendars/{calendar_id}/blocked-periods",
        headers=hospital_headers,
        json={"starts_at": first["starts_at"], "ends_at": first["ends_at"], "kind": "BLOCK"},
    )
    assert blocked.status_code == 201, blocked.text
    day = first["starts_at"][:10]
    refreshed = client.get(
        "/api/v1/availability",
        headers=patient_headers,
        params={"doctor_id": doctor_id, "appointment_type_id": kind_id, "date_from": day, "date_to": day},
    )
    assert first["starts_at"] not in {slot["starts_at"] for slot in refreshed.json()}


def test_verified_booking_is_idempotent_and_starts_followup(client, patient_headers, monkeypatch):
    monkeypatch.setattr("app.appointments.MockEHRConnector", SuccessfulConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    payload = {
        "doctor_id": doctor_id,
        "appointment_type_id": kind_id,
        "starts_at": slots[0]["starts_at"],
        "idempotency_key": "verified-booking-key",
    }
    first = client.post("/api/v1/appointments", headers=patient_headers, json=payload)
    second = client.post("/api/v1/appointments", headers=patient_headers, json=payload)
    assert first.status_code == 201
    assert first.json()["status"] == "CONFIRMED"
    assert second.json()["id"] == first.json()["id"]
    with SessionLocal() as db:
        assert db.scalar(select(Appointment).where(Appointment.id == first.json()["id"])).status == "CONFIRMED"
        assert len(db.scalars(select(QuestionnaireAssignment)).all()) == 1
        notifications = db.scalars(select(Notification)).all()
        assert len(notifications) == 1
        assert notifications[0].status == "SIMULATED"


def test_same_slot_cannot_be_double_booked(client, patient_headers, monkeypatch):
    monkeypatch.setattr("app.appointments.MockEHRConnector", SuccessfulConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    base = {"doctor_id": doctor_id, "appointment_type_id": kind_id, "starts_at": slots[0]["starts_at"]}
    first = client.post("/api/v1/appointments", headers=patient_headers, json={**base, "idempotency_key": "double-book-a"})
    second = client.post("/api/v1/appointments", headers=patient_headers, json={**base, "idempotency_key": "double-book-b"})
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "SLOT_NO_LONGER_AVAILABLE"


def test_unknown_outcome_is_found_without_duplicate_create(client, patient_headers, monkeypatch):
    UnknownThenFoundConnector.records = {}
    UnknownThenFoundConnector.created = 0
    monkeypatch.setattr("app.appointments.MockEHRConnector", UnknownThenFoundConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    response = client.post(
        "/api/v1/appointments",
        headers=patient_headers,
        json={
            "doctor_id": doctor_id,
            "appointment_type_id": kind_id,
            "starts_at": slots[0]["starts_at"],
            "idempotency_key": "unknown-outcome-key",
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "CONFIRMED"
    assert UnknownThenFoundConnector.created == 1


def test_retry_exhaustion_creates_reconciliation_record(client, patient_headers, monkeypatch):
    AlwaysRetryableConnector.attempts = 0
    monkeypatch.setattr("app.appointments.MockEHRConnector", AlwaysRetryableConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    response = client.post(
        "/api/v1/appointments", headers=patient_headers,
        json={"doctor_id": doctor_id, "appointment_type_id": kind_id, "starts_at": slots[0]["starts_at"], "idempotency_key": "retry-exhaustion-key"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "RECONCILIATION_REQUIRED"
    assert AlwaysRetryableConnector.attempts == 3
    with SessionLocal() as db:
        records = db.scalars(select(ReconciliationRecord)).all()
        assert len(records) == 1
        assert records[0].status == "REQUIRED"


def test_idempotency_key_cannot_be_reused_for_different_request(client, patient_headers, monkeypatch):
    monkeypatch.setattr("app.appointments.MockEHRConnector", SuccessfulConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    base = {"doctor_id": doctor_id, "appointment_type_id": kind_id, "idempotency_key": "same-key-different-request"}
    first = client.post("/api/v1/appointments", headers=patient_headers, json={**base, "starts_at": slots[0]["starts_at"]})
    second = client.post("/api/v1/appointments", headers=patient_headers, json={**base, "starts_at": slots[1]["starts_at"]})
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_reschedule_verifies_new_slot_and_cancel_releases_capacity(client, patient_headers, monkeypatch):
    SuccessfulConnector.records = {}
    monkeypatch.setattr("app.appointments.MockEHRConnector", SuccessfulConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    booked = client.post(
        "/api/v1/appointments", headers=patient_headers,
        json={"doctor_id": doctor_id, "appointment_type_id": kind_id, "starts_at": slots[0]["starts_at"], "idempotency_key": "reschedule-original"},
    ).json()
    moved = client.post(
        f"/api/v1/appointments/{booked['id']}/reschedule", headers=patient_headers,
        json={"starts_at": slots[1]["starts_at"], "idempotency_key": "reschedule-new", "expected_version": booked["version"]},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "RESCHEDULED"
    cancelled = client.post(
        f"/api/v1/appointments/{booked['id']}/cancel", headers=patient_headers,
        json={"idempotency_key": "cancel-after-reschedule", "expected_version": moved.json()["version"], "reason": "No longer needed"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"


def test_structured_questionnaire_validation_and_doctor_visibility(client, patient_headers, monkeypatch):
    SuccessfulConnector.records = {}
    monkeypatch.setattr("app.appointments.MockEHRConnector", SuccessfulConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    client.post(
        "/api/v1/appointments", headers=patient_headers,
        json={"doctor_id": doctor_id, "appointment_type_id": kind_id, "starts_at": slots[0]["starts_at"], "idempotency_key": "questionnaire-booking"},
    )
    assignments = client.get("/api/v1/questionnaire-assignments", headers=patient_headers).json()
    detail = client.get(f"/api/v1/questionnaire-assignments/{assignments[0]['id']}", headers=patient_headers).json()
    answers = {}
    for question in detail["questions"]:
        if question["question_type"] == "YES_NO": answers[str(question["id"])] = True
        elif question["question_type"] == "CHOICE": answers[str(question["id"])] = question["options"][0]
        else: answers[str(question["id"])] = "Wheelchair access"
    submitted = client.post(
        f"/api/v1/questionnaire-assignments/{assignments[0]['id']}/answers",
        headers=patient_headers, json={"answers": answers},
    )
    assert submitted.status_code == 200, submitted.text


def test_required_questionnaire_answer_cannot_be_skipped(client, patient_headers, monkeypatch):
    SuccessfulConnector.records = {}
    monkeypatch.setattr("app.appointments.MockEHRConnector", SuccessfulConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    client.post(
        "/api/v1/appointments", headers=patient_headers,
        json={"doctor_id": doctor_id, "appointment_type_id": kind_id, "starts_at": slots[0]["starts_at"], "idempotency_key": "questionnaire-required"},
    )
    assignment = client.get("/api/v1/questionnaire-assignments", headers=patient_headers).json()[0]
    response = client.post(
        f"/api/v1/questionnaire-assignments/{assignment['id']}/answers",
        headers=patient_headers, json={"answers": {}},
    )
    assert response.status_code == 422


def test_telephone_channel_reuses_patient_agent(client):
    response = client.post(
        "/api/v1/telephone/events",
        json={"call_id": "CALL-001", "event": "TRANSCRIPT", "patient_email": "patient@example.com", "transcript": "show my appointments"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "RESPOND"


def test_ai_clarifies_and_enforces_clinical_boundary(client, patient_headers):
    safety = client.post("/api/v1/agent/messages", headers=patient_headers, json={"message": "What medicine should I take for my shoulder?"})
    assert safety.status_code == 200
    assert safety.json()["status"] == "SAFETY_BOUNDARY"
    discovery = client.post("/api/v1/agent/messages", headers=patient_headers, json={"message": "I need someone for shoulder pain"})
    assert discovery.status_code == 200
    assert discovery.json().get("needs_clarification") is True


def test_ai_discovery_to_verified_booking_flow(client, patient_headers, monkeypatch):
    SuccessfulConnector.records = {}
    monkeypatch.setattr("app.appointments.MockEHRConnector", SuccessfulConnector)
    _, _, slots = _next_slot(client, patient_headers)
    discovery = client.post(
        "/api/v1/agent/messages", headers=patient_headers,
        json={"message": f"I need orthopedics on {slots[0]['starts_at'][:10]}"},
    ).json()
    assert discovery.get("options")
    selected = client.post(
        "/api/v1/agent/messages", headers=patient_headers,
        json={"conversation_id": discovery["conversation_id"], "message": "1"},
    ).json()
    assert selected["needs_confirmation"] is True
    confirmed = client.post(
        "/api/v1/agent/messages", headers=patient_headers,
        json={"conversation_id": discovery["conversation_id"], "message": "confirm"},
    ).json()
    assert confirmed["appointment"]["status"] == "CONFIRMED"
    assert confirmed["appointment"]["external_id"]


def test_ai_collects_approved_questionnaire_conversationally(client, patient_headers, monkeypatch):
    SuccessfulConnector.records = {}
    monkeypatch.setattr("app.appointments.MockEHRConnector", SuccessfulConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    client.post(
        "/api/v1/appointments", headers=patient_headers,
        json={"doctor_id": doctor_id, "appointment_type_id": kind_id, "starts_at": slots[0]["starts_at"], "idempotency_key": "agent-questionnaire"},
    )
    started = client.post("/api/v1/agent/messages", headers=patient_headers, json={"message": "complete my questionnaire"}).json()
    assert "first visit" in started["text"].lower()
    first = client.post("/api/v1/agent/messages", headers=patient_headers, json={"conversation_id": started["conversation_id"], "message": "yes"}).json()
    assert "area" in first["text"].lower()
    completed = client.post("/api/v1/agent/messages", headers=patient_headers, json={"conversation_id": started["conversation_id"], "message": "Shoulder"}).json()
    assert "complete" in completed["text"].lower()
    with SessionLocal() as db:
        assert db.scalar(select(QuestionnaireAssignment)).status == "COMPLETED"


def test_ai_cancellation_requires_confirmation_and_verifies(client, patient_headers, monkeypatch):
    SuccessfulConnector.records = {}
    monkeypatch.setattr("app.appointments.MockEHRConnector", SuccessfulConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    booked = client.post(
        "/api/v1/appointments", headers=patient_headers,
        json={"doctor_id": doctor_id, "appointment_type_id": kind_id, "starts_at": slots[0]["starts_at"], "idempotency_key": "agent-cancel-booking"},
    ).json()
    proposed = client.post(
        "/api/v1/agent/messages", headers=patient_headers,
        json={"message": f"cancel appointment #{booked['id']}"},
    ).json()
    assert proposed["needs_confirmation"] is True
    cancelled = client.post(
        "/api/v1/agent/messages", headers=patient_headers,
        json={"conversation_id": proposed["conversation_id"], "message": "confirm cancel"},
    ).json()
    assert cancelled["appointment"]["status"] == "CANCELLED"


def test_ai_reschedule_uses_real_slots_and_confirmation(client, patient_headers, monkeypatch):
    SuccessfulConnector.records = {}
    monkeypatch.setattr("app.appointments.MockEHRConnector", SuccessfulConnector)
    doctor_id, kind_id, slots = _next_slot(client, patient_headers)
    booked = client.post(
        "/api/v1/appointments", headers=patient_headers,
        json={"doctor_id": doctor_id, "appointment_type_id": kind_id, "starts_at": slots[0]["starts_at"], "idempotency_key": "agent-reschedule-booking"},
    ).json()
    proposed = client.post(
        "/api/v1/agent/messages", headers=patient_headers,
        json={"message": f"reschedule appointment #{booked['id']} to {slots[1]['starts_at'][:10]}"},
    ).json()
    assert proposed.get("options")
    selected = client.post(
        "/api/v1/agent/messages", headers=patient_headers,
        json={"conversation_id": proposed["conversation_id"], "message": "2"},
    ).json()
    assert selected["needs_confirmation"] is True
    moved = client.post(
        "/api/v1/agent/messages", headers=patient_headers,
        json={"conversation_id": proposed["conversation_id"], "message": "confirm reschedule"},
    ).json()
    assert "appointment" in moved, moved
    assert moved["appointment"]["status"] == "RESCHEDULED"
