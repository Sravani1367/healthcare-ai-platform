import os
import uuid

from fastapi.testclient import TestClient

import mock_ehr


def test_mock_ehr_create_is_idempotent():
    client = TestClient(mock_ehr.ehr_app)
    key = f"pytest-{uuid.uuid4().hex}"
    params = {"patient_id": 81, "doctor_id": 91, "date": "2030-01-02", "time": "09:00", "idempotency_key": key}
    first = client.post("/ehr/appointments", params=params)
    second = client.post("/ehr/appointments", params=params)
    assert first.status_code == 200
    assert first.json()["external_id"] == second.json()["external_id"]
    assert second.json()["replayed"] is True


def test_mock_ehr_unknown_outcome_is_searchable():
    client = TestClient(mock_ehr.ehr_app)
    client.post("/ehr/simulation", params={"unknown": True})
    key = f"pytest-{uuid.uuid4().hex}"
    created = client.post("/ehr/appointments", params={"patient_id": 82, "doctor_id": 92, "date": "2030-01-03", "time": "10:00", "idempotency_key": key})
    assert created.json()["success"] is False
    found = client.get("/ehr/appointments/search", params={"patient_id": 82, "doctor_id": 92, "date": "2030-01-03", "time": "10:00"})
    assert found.json()["exists"] is True
    client.post("/ehr/simulation", params={"unknown": False})

