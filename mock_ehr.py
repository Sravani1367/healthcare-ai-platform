from fastapi import FastAPI
import sqlite3
import os
import uuid


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

def get_db():
    db_path = os.getenv("MOCK_EHR_DB_PATH") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "mock_ehr.db"
    )
    return sqlite3.connect(db_path)


def init_db():
    db = get_db()

    db.execute("""
        CREATE TABLE IF NOT EXISTS appointments (
            external_id TEXT PRIMARY KEY,
            patient_id INTEGER,
            doctor_id INTEGER,
            date TEXT,
            time TEXT,
            status TEXT,
            idempotency_key TEXT
        )
    """)

    columns = {
        row[1]
        for row in db.execute("PRAGMA table_info(appointments)").fetchall()
    }
    if "idempotency_key" not in columns:
        db.execute("ALTER TABLE appointments ADD COLUMN idempotency_key TEXT")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_ehr_idempotency "
        "ON appointments(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )

    db.commit()
    db.close()


init_db()


# --------------------------------------------------
# DATABASE HELPERS
# --------------------------------------------------

def save_appointment(
    external_id,
    patient_id,
    doctor_id,
    date,
    time,
    status,
    idempotency_key=None
):
    db = get_db()

    db.execute(
        """
        INSERT INTO appointments
        (external_id, patient_id, doctor_id, date, time, status, idempotency_key)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            external_id,
            patient_id,
            doctor_id,
            date,
            time,
            status,
            idempotency_key
        )
    )

    db.commit()
    db.close()


def get_by_idempotency_key(idempotency_key):
    if not idempotency_key:
        return None
    db = get_db()
    row = db.execute(
        """
        SELECT external_id, patient_id, doctor_id, date, time, status
        FROM appointments
        WHERE idempotency_key = ?
        """,
        (idempotency_key,),
    ).fetchone()
    db.close()
    if row is None:
        return None
    return {
        "external_id": row[0], "patient_id": row[1], "doctor_id": row[2],
        "date": row[3], "time": row[4], "status": row[5],
    }


def get_db_appointment(external_id):
    db = get_db()

    row = db.execute(
        """
        SELECT external_id, patient_id, doctor_id, date, time, status
        FROM appointments
        WHERE external_id = ?
        """,
        (external_id,)
    ).fetchone()

    db.close()

    if row is None:
        return None

    return {
        "external_id": row[0],
        "patient_id": row[1],
        "doctor_id": row[2],
        "date": row[3],
        "time": row[4],
        "status": row[5]
    }


# --------------------------------------------------
# FASTAPI APP
# --------------------------------------------------

ehr_app = FastAPI(title="Mock EHR")


@ehr_app.get("/health")
def health():
    return {"status": "healthy", "service": "mock-ehr"}


# Deterministic lookup resources keep the mock replaceable by a real connector
# without requiring a second administrative database for the prototype.
@ehr_app.get("/ehr/patients/{internal_id}")
def lookup_patient(internal_id: int):
    return {"success": True, "external_id": f"PAT-{internal_id}", "active": True}


@ehr_app.get("/ehr/providers/{internal_id}")
def lookup_provider(internal_id: int):
    return {"success": True, "external_id": f"PROV-{internal_id}", "active": True}


@ehr_app.get("/ehr/facilities/{internal_id}")
def lookup_facility(internal_id: int):
    return {"success": True, "external_id": f"FAC-{internal_id}", "active": True}


@ehr_app.get("/ehr/departments/{internal_id}")
def lookup_department(internal_id: int):
    return {"success": True, "external_id": f"DEPT-{internal_id}", "active": True}


@ehr_app.get("/ehr/calendars/{internal_id}")
def lookup_calendar(internal_id: int):
    return {"success": True, "external_id": f"CAL-{internal_id}", "active": True}


@ehr_app.get("/ehr/availability")
def lookup_availability(doctor_id: int, date: str):
    return {"success": True, "doctor_id": doctor_id, "date": date, "constraints_satisfied": True}


# --------------------------------------------------
# FAILURE SIMULATION STATE
# --------------------------------------------------

simulate_failure = False
simulate_unknown = False


# --------------------------------------------------
# FAILURE SIMULATION CONTROL
# --------------------------------------------------

@ehr_app.post("/ehr/simulation")
def configure_simulation(
    failure: bool = False,
    unknown: bool = False
):
    global simulate_failure
    global simulate_unknown

    simulate_failure = failure
    simulate_unknown = unknown

    return {
        "success": True,
        "simulate_failure": simulate_failure,
        "simulate_unknown": simulate_unknown
    }


@ehr_app.get("/ehr/simulation")
def get_simulation_status():
    return {
        "simulate_failure": simulate_failure,
        "simulate_unknown": simulate_unknown
    }


# --------------------------------------------------
# CREATE APPOINTMENT
# --------------------------------------------------

@ehr_app.post("/ehr/appointments")
def create_ehr_appointment(
    patient_id: int,
    doctor_id: int,
    date: str,
    time: str,
    idempotency_key: str = None
):
    return create_external_appointment(
        patient_id,
        doctor_id,
        date,
        time,
        idempotency_key
    )


def create_external_appointment(
    patient_id,
    doctor_id,
    date,
    time,
    idempotency_key=None
):

    existing = get_by_idempotency_key(idempotency_key)
    if existing is not None:
        return {
            "success": True,
            "external_id": existing["external_id"],
            "appointment": existing,
            "replayed": True,
        }

    # Complete EHR failure:
    # appointment is NOT created.
    if simulate_failure:
        return {
            "success": False,
            "message": "Mock EHR temporarily unavailable"
        }

    external_id = f"EHR-{uuid.uuid4().hex[:12].upper()}"

    # Save appointment permanently.
    save_appointment(
        external_id,
        patient_id,
        doctor_id,
        date,
        time,
        "CONFIRMED",
        idempotency_key
    )

    appointment = get_db_appointment(
        external_id
    )

    # Unknown outcome:
    # EHR successfully created the appointment,
    # but the response is lost.
    if simulate_unknown:
        return {
            "success": False,
            "message": "EHR created appointment but response was lost"
        }

    return {
        "success": True,
        "external_id": external_id,
        "appointment": appointment
    }


# --------------------------------------------------
# SEARCH APPOINTMENT
# --------------------------------------------------

@ehr_app.get("/ehr/appointments/search")
def search_ehr_appointment(
    patient_id: int,
    doctor_id: int,
    date: str,
    time: str
):

    db = get_db()

    row = db.execute(
        """
        SELECT external_id, patient_id, doctor_id, date, time, status
        FROM appointments
        WHERE patient_id = ?
        AND doctor_id = ?
        AND date = ?
        AND time = ?
        """,
        (
            patient_id,
            doctor_id,
            date,
            time
        )
    ).fetchone()

    db.close()

    if row is None:
        return {
            "exists": False,
            "external_id": None,
            "appointment": None
        }

    appointment = {
        "external_id": row[0],
        "patient_id": row[1],
        "doctor_id": row[2],
        "date": row[3],
        "time": row[4],
        "status": row[5]
    }

    return {
        "exists": True,
        "external_id": row[0],
        "appointment": appointment
    }


# --------------------------------------------------
# VERIFY APPOINTMENT
# --------------------------------------------------

@ehr_app.get("/ehr/appointments/{external_id}")
def get_ehr_appointment(external_id: str):

    return verify_external_appointment(
        external_id
    )


def verify_external_appointment(external_id):

    appointment = get_db_appointment(
        external_id
    )

    if appointment is None:
        return {
            "exists": False,
            "appointment": None
        }

    return {
        "exists": True,
        "appointment": appointment
    }


# --------------------------------------------------
# CANCEL APPOINTMENT
# --------------------------------------------------

@ehr_app.post("/ehr/appointments/{external_id}/cancel")
def cancel_ehr_appointment(external_id: str, idempotency_key: str = None):

    appointment = get_db_appointment(
        external_id
    )

    if appointment is None:
        return {
            "success": False,
            "message": "External appointment not found"
        }

    db = get_db()

    db.execute(
        """
        UPDATE appointments
        SET status = ?
        WHERE external_id = ?
        """,
        (
            "CANCELLED",
            external_id
        )
    )

    db.commit()
    db.close()

    appointment["status"] = "CANCELLED"

    return {
        "success": True,
        "external_id": external_id,
        "appointment": appointment
    }


# --------------------------------------------------
# RESCHEDULE APPOINTMENT
# --------------------------------------------------

@ehr_app.post("/ehr/appointments/{external_id}/reschedule")
def reschedule_ehr_appointment(
    external_id: str,
    date: str,
    time: str,
    idempotency_key: str = None
):

    appointment = get_db_appointment(
        external_id
    )

    if appointment is None:
        return {
            "success": False,
            "message": "External appointment not found"
        }

    db = get_db()

    db.execute(
        """
        UPDATE appointments
        SET date = ?,
            time = ?,
            status = ?
        WHERE external_id = ?
        """,
        (
            date,
            time,
            "CONFIRMED",
            external_id
        )
    )

    db.commit()
    db.close()

    appointment["date"] = date
    appointment["time"] = time
    appointment["status"] = "CONFIRMED"

    return {
        "success": True,
        "external_id": external_id,
        "appointment": appointment
    }


@ehr_app.patch("/ehr/appointments/{external_id}")
def update_ehr_appointment(external_id: str, date: str, time: str, idempotency_key: str = None):
    """Generic update operation; rescheduling remains as an explicit convenience endpoint."""
    return reschedule_ehr_appointment(external_id, date, time, idempotency_key)
