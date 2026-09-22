"""
Seed script for the Healthcare AI Platform demo database.

Run this from inside your backend/ folder, with your venv active,
BEFORE starting uvicorn:

    python seed_data.py

It is safe to re-run: if hospitals already exist, it skips seeding
so you don't end up with duplicate data every time you restart.
"""

from datetime import date, timedelta

from database import engine, SessionLocal
from models import Base, Hospital, Doctor, Availability, Patient


Base.metadata.create_all(bind=engine)

db = SessionLocal()

try:

    existing_hospitals = db.query(Hospital).count()

    if existing_hospitals > 0:
        print(
            f"Database already has {existing_hospitals} hospital(s). "
            "Skipping seed to avoid duplicates. "
            "Delete healthcare.db first if you want a completely fresh seed."
        )

    else:

        # ============================================================
        # HOSPITALS
        # ============================================================

        city_hospital = Hospital(
            name="City General Hospital",
            address="123 Main Street, Springfield",
            contact="+1-555-0100",
            status="ACTIVE"
        )

        sunrise_clinic = Hospital(
            name="Sunrise Family Clinic",
            address="45 Riverside Ave, Springfield",
            contact="+1-555-0200",
            status="ACTIVE"
        )

        db.add_all([city_hospital, sunrise_clinic])
        db.commit()
        db.refresh(city_hospital)
        db.refresh(sunrise_clinic)

        print(
            f"Created hospitals: "
            f"{city_hospital.id} - {city_hospital.name}, "
            f"{sunrise_clinic.id} - {sunrise_clinic.name}"
        )

        # ============================================================
        # DOCTORS
        #
        # Two Orthopedics doctors at DIFFERENT hospitals on purpose,
        # so booking "shoulder pain" triggers the doctor-disambiguation
        # flow (agent.py should ask which one you mean, not guess).
        # ============================================================

        doctors_data = [
            {
                "hospital_id": city_hospital.id,
                "name": "Dr. Alice Kumar",
                "specialty": "Orthopedics",
                "department": "Orthopedics",
                "qualifications": "MBBS, MS Ortho",
                "experience": "12 years",
                "languages": "English, Hindi"
            },
            {
                "hospital_id": sunrise_clinic.id,
                "name": "Dr. Ben Foster",
                "specialty": "Orthopedics",
                "department": "Orthopedics",
                "qualifications": "MBBS, MS Ortho",
                "experience": "8 years",
                "languages": "English"
            },
            {
                "hospital_id": city_hospital.id,
                "name": "Dr. Carla Mendes",
                "specialty": "Cardiology",
                "department": "Cardiology",
                "qualifications": "MBBS, MD Cardiology",
                "experience": "15 years",
                "languages": "English, Spanish"
            },
            {
                "hospital_id": sunrise_clinic.id,
                "name": "Dr. David Chen",
                "specialty": "Dermatology",
                "department": "Dermatology",
                "qualifications": "MBBS, MD Dermatology",
                "experience": "6 years",
                "languages": "English, Mandarin"
            },
            {
                "hospital_id": city_hospital.id,
                "name": "Dr. Priya Nair",
                "specialty": "Pediatrics",
                "department": "Pediatrics",
                "qualifications": "MBBS, MD Pediatrics",
                "experience": "10 years",
                "languages": "English, Hindi, Tamil"
            },
        ]

        doctors = []

        for data in doctors_data:

            doctor = Doctor(
                hospital_id=data["hospital_id"],
                name=data["name"],
                specialty=data["specialty"],
                department=data["department"],
                qualifications=data["qualifications"],
                experience=data["experience"],
                languages=data["languages"],
                status="ACTIVE"
            )

            db.add(doctor)
            doctors.append(doctor)

        db.commit()

        for doctor in doctors:
            db.refresh(doctor)
            print(
                f"Created doctor: {doctor.id} - {doctor.name} "
                f"({doctor.specialty}) at hospital {doctor.hospital_id}"
            )

        # ============================================================
        # AVAILABILITY
        #
        # Next 5 days, three 30-minute slots per day, for every doctor.
        # ============================================================

        time_slots = [
            ("09:00", "09:30"),
            ("09:30", "10:00"),
            ("10:00", "10:30"),
            ("11:00", "11:30"),
            ("14:00", "14:30"),
        ]

        slot_count = 0

        for doctor in doctors:

            for day_offset in range(1, 6):  # tomorrow through +5 days

                slot_date = str(date.today() + timedelta(days=day_offset))

                for start_time, end_time in time_slots:

                    availability = Availability(
                        doctor_id=doctor.id,
                        date=slot_date,
                        start_time=start_time,
                        end_time=end_time,
                        status="AVAILABLE"
                    )

                    db.add(availability)
                    slot_count += 1

        db.commit()

        print(f"Created {slot_count} availability slots.")

        # ============================================================
        # DEMO PATIENT
        #
        # agent.py's DEMO_PATIENT_ID is 1. Since this is the first
        # row inserted into an empty patients table, its id will be 1.
        # ============================================================

        demo_patient = Patient(
            hospital_id=city_hospital.id,
            name="Jordan Patient",
            phone="+1-555-0900",
            email="jordan.patient@example.com",
            date_of_birth="1990-05-14",
            communication_preference="SMS",
            external_patient_id=None
        )

        db.add(demo_patient)
        db.commit()
        db.refresh(demo_patient)

        print(
            f"Created demo patient: {demo_patient.id} - {demo_patient.name}"
        )

        if demo_patient.id != 1:
            print(
                "WARNING: demo patient id is "
                f"{demo_patient.id}, not 1. "
                "agent.py's DEMO_PATIENT_ID is hardcoded to 1 - "
                "update that constant in agent.py/capabilities.py "
                "to match, or delete healthcare.db and re-run this "
                "seed script against a truly empty database."
            )

        print("\nSeed complete. You can now start uvicorn and streamlit.")

finally:
    db.close()
