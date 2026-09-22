import os

os.environ["DATABASE_URL"] = "sqlite:////tmp/healthcare_platform_pytest.db"
os.environ["TOKEN_SECRET"] = "pytest-secret"
os.environ["ALLOW_DEMO_SEED"] = "true"
os.environ.pop("RESEND_API_KEY", None)

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.seed import seed_demo


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_demo(db)
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client, email, password):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def patient_headers(client):
    return login(client, "patient@example.com", "DemoPatient!2026")


@pytest.fixture
def hospital_headers(client):
    return login(client, "hospital.admin@example.com", "DemoHospital!2026")


@pytest.fixture
def platform_headers(client):
    return login(client, "platform.admin@example.com", "DemoAdmin!2026")

