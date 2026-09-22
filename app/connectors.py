from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class ConnectorResult:
    classification: str
    data: dict[str, Any]
    message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "SUCCESS"


class MockEHRConnector:
    """Vendor-neutral adapter for the separately deployed mock EHR."""

    def __init__(self, base_url: str, timeout_seconds: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, **kwargs) -> ConnectorResult:
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout_seconds,
                **kwargs,
            )
            if response.status_code == 429:
                return ConnectorResult("RETRYABLE", {}, "EHR rate limit")
            if response.status_code in {401, 403}:
                return ConnectorResult("NON_RETRYABLE", {}, "EHR authorization failure")
            if response.status_code >= 500:
                return ConnectorResult("RETRYABLE", {}, "EHR unavailable")
            if response.status_code >= 400:
                return ConnectorResult("NON_RETRYABLE", {}, "EHR rejected request")
            data = response.json()
            if data.get("success") is False:
                message = str(data.get("message", "External operation failed"))
                classification = (
                    "UNKNOWN_OUTCOME"
                    if "response was lost" in message.lower() or "unknown" in message.lower()
                    else "RETRYABLE"
                )
                return ConnectorResult(classification, data, message)
            return ConnectorResult("SUCCESS", data)
        except requests.Timeout:
            return ConnectorResult("UNKNOWN_OUTCOME", {}, "External request timed out")
        except requests.ConnectionError:
            return ConnectorResult("RETRYABLE", {}, "External system is unreachable")
        except (requests.RequestException, ValueError) as exc:
            return ConnectorResult("NON_RETRYABLE", {}, str(exc))

    def lookup_patient(self, internal_patient_id: int) -> ConnectorResult:
        return self._request("GET", f"/ehr/patients/{internal_patient_id}")

    def lookup_provider(self, internal_doctor_id: int) -> ConnectorResult:
        return self._request("GET", f"/ehr/providers/{internal_doctor_id}")

    def lookup_facility(self, internal_hospital_id: int) -> ConnectorResult:
        return self._request("GET", f"/ehr/facilities/{internal_hospital_id}")

    def lookup_department(self, internal_department_id: int) -> ConnectorResult:
        return self._request("GET", f"/ehr/departments/{internal_department_id}")

    def lookup_calendar(self, internal_calendar_id: int) -> ConnectorResult:
        return self._request("GET", f"/ehr/calendars/{internal_calendar_id}")

    def find_availability(self, *, doctor_id: int, date: str) -> ConnectorResult:
        return self._request("GET", "/ehr/availability", params={"doctor_id": doctor_id, "date": date})

    def create_appointment(
        self,
        *,
        patient_id: int,
        doctor_id: int,
        date: str,
        time: str,
        idempotency_key: str,
    ) -> ConnectorResult:
        return self._request(
            "POST",
            "/ehr/appointments",
            params={
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "date": date,
                "time": time,
                "idempotency_key": idempotency_key,
            },
        )

    def get_appointment(self, external_id: str) -> ConnectorResult:
        result = self._request("GET", f"/ehr/appointments/{external_id}")
        if result.succeeded and not result.data.get("exists", True):
            return ConnectorResult("ABSENT", result.data)
        return result

    def search_appointment(
        self, *, patient_id: int, doctor_id: int, date: str, time: str
    ) -> ConnectorResult:
        result = self._request(
            "GET",
            "/ehr/appointments/search",
            params={
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "date": date,
                "time": time,
            },
        )
        if result.succeeded and not result.data.get("exists", False):
            return ConnectorResult("ABSENT", result.data)
        return result

    def reschedule_appointment(
        self, *, external_id: str, date: str, time: str, idempotency_key: str
    ) -> ConnectorResult:
        return self._request(
            "POST",
            f"/ehr/appointments/{external_id}/reschedule",
            params={"date": date, "time": time, "idempotency_key": idempotency_key},
        )

    def cancel_appointment(
        self, *, external_id: str, idempotency_key: str
    ) -> ConnectorResult:
        return self._request(
            "POST",
            f"/ehr/appointments/{external_id}/cancel",
            params={"idempotency_key": idempotency_key},
        )

    def verify_appointment(
        self, *, external_id: str, expected: dict[str, Any]
    ) -> ConnectorResult:
        result = self.get_appointment(external_id)
        if not result.succeeded:
            return result
        appointment = result.data.get("appointment") or {}
        mismatches = {
            key: {"expected": value, "actual": appointment.get(key)}
            for key, value in expected.items()
            if str(appointment.get(key)) != str(value)
        }
        if mismatches:
            return ConnectorResult("MISMATCH", {"mismatches": mismatches})
        return ConnectorResult("SUCCESS", result.data)
