import requests


# Mock EHR server
MOCK_EHR_URL = "http://127.0.0.1:8001"


# =========================================================
# CREATE APPOINTMENT IN EXTERNAL EHR
# =========================================================

def create_external_appointment(patient_id, doctor_id, date, time):
    try:
        response = requests.post(
            f"{MOCK_EHR_URL}/ehr/appointments",
            params={
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "date": date,
                "time": time
            },
            timeout=5
        )

        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "message": f"Could not connect to Mock EHR: {str(e)}"
        }


# =========================================================
# VERIFY EXTERNAL APPOINTMENT
# =========================================================

def verify_external_appointment(external_id):
    try:
        response = requests.get(
            f"{MOCK_EHR_URL}/ehr/appointments/{external_id}",
            timeout=5
        )

        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        return {
            "exists": False,
            "appointment": None,
            "message": f"Could not verify appointment: {str(e)}"
        }


# =========================================================
# SEARCH APPOINTMENT IN EXTERNAL EHR
# Used for reconciliation when booking result is uncertain
# =========================================================

def search_external_appointment(
    patient_id,
    doctor_id,
    date,
    time
):
    try:
        response = requests.get(
            f"{MOCK_EHR_URL}/ehr/appointments/search",
            params={
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "date": date,
                "time": time
            },
            timeout=5
        )

        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        return {
            "exists": False,
            "external_id": None,
            "appointment": None,
            "message": f"Could not search Mock EHR: {str(e)}"
        }


# =========================================================
# CANCEL APPOINTMENT IN EXTERNAL EHR
# =========================================================

def cancel_external_appointment(external_id):

    try:
        response = requests.post(
            f"{MOCK_EHR_URL}/ehr/appointments/{external_id}/cancel",
            timeout=5
        )

        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "message": f"Could not cancel appointment in Mock EHR: {str(e)}"
        }



# =========================================================
# RESCHEDULE APPOINTMENT IN EXTERNAL EHR
# =========================================================

def reschedule_external_appointment(
    external_id,
    date,
    time
):
    try:
        response = requests.post(
            f"{MOCK_EHR_URL}/ehr/appointments/{external_id}/reschedule",
            params={
                "date": date,
                "time": time
            },
            timeout=5
        )

        response.raise_for_status()

        return response.json()

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "message": (
                f"Could not reschedule appointment "
                f"in Mock EHR: {str(e)}"
            )
        }