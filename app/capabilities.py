from __future__ import annotations

import json
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .appointments import cancel_appointment, create_appointment, reschedule_appointment
from .models import (
    Appointment,
    Doctor,
    Hospital,
    Patient,
    Questionnaire,
    QuestionnaireAssignment,
    QuestionnaireQuestion,
    QuestionnaireResponse,
    Specialty,
    UserPreference,
)
from .scheduling import list_available_slots
from .security import RequestContext
from .telemetry import record_capability


class CapabilityRunner:
    """The only interface through which the conversational agent may act."""

    def __init__(self, db: Session, ctx: RequestContext):
        self.db = db
        self.ctx = ctx

    def search_hospitals(self, query: str | None = None) -> list[dict]:
        statement = select(Hospital).where(Hospital.status == "APPROVED")
        if query:
            statement = statement.where(Hospital.name.ilike(f"%{query}%"))
        items = self.db.scalars(statement.order_by(Hospital.name)).all()
        result = [{"id": x.id, "name": x.name, "address": x.address} for x in items]
        record_capability(self.db, self.ctx, "search_hospitals", {"query": query}, "SUCCEEDED")
        return result

    def search_doctors(
        self, *, specialty: str | None = None, hospital_id: int | None = None
    ) -> list[dict]:
        statement = (
            select(Doctor, Specialty, Hospital)
            .join(Specialty, Specialty.id == Doctor.specialty_id)
            .join(Hospital, Hospital.id == Doctor.hospital_id)
            .where(Doctor.status == "ACTIVE", Hospital.status == "APPROVED")
        )
        if specialty:
            statement = statement.where(Specialty.name.ilike(f"%{specialty}%"))
        if hospital_id:
            statement = statement.where(Doctor.hospital_id == hospital_id)
        rows = self.db.execute(statement.order_by(Doctor.name)).all()
        result = [{
            "id": doctor.id,
            "name": doctor.name,
            "hospital_id": hospital.id,
            "hospital": hospital.name,
            "specialty_id": specialty_row.id,
            "specialty": specialty_row.name,
            "languages": __import__("json").loads(doctor.languages_json or "[]"),
        } for doctor, specialty_row, hospital in rows]
        record_capability(
            self.db, self.ctx, "search_doctors",
            {"specialty": specialty, "hospital_id": hospital_id}, "SUCCEEDED",
            hospital_id=hospital_id,
        )
        return result

    def check_availability(
        self,
        *,
        doctor_id: int,
        appointment_type_id: int,
        date_from: date,
        date_to: date,
    ) -> list[dict]:
        result = list_available_slots(
            self.db,
            doctor_id=doctor_id,
            appointment_type_id=appointment_type_id,
            date_from=date_from,
            date_to=date_to,
        )
        hospital_id = result[0]["hospital_id"] if result else None
        record_capability(
            self.db, self.ctx, "check_availability",
            {
                "doctor_id": doctor_id,
                "appointment_type_id": appointment_type_id,
                "date_from": str(date_from),
                "date_to": str(date_to),
            },
            "SUCCEEDED",
            hospital_id=hospital_id,
        )
        return result

    def create_appointment(
        self,
        *,
        doctor_id: int,
        appointment_type_id: int,
        starts_at: datetime,
        idempotency_key: str,
        slot_quote_id: str,
        confirmation_token: str,
    ) -> dict:
        return create_appointment(
            self.db,
            self.ctx,
            doctor_id=doctor_id,
            appointment_type_id=appointment_type_id,
            starts_at=starts_at,
            idempotency_key=idempotency_key,
            slot_quote_id=slot_quote_id,
            confirmation_token=confirmation_token,
        )

    def get_appointment(self, appointment_id: int) -> dict:
        appointment = self.db.get(Appointment, appointment_id)
        if appointment is None:
            raise HTTPException(status_code=404, detail={"code": "RESOURCE_NOT_FOUND", "message": "Appointment not found"})
        if self.ctx.role == "PATIENT" and appointment.patient_id != self.ctx.patient_id:
            raise HTTPException(status_code=404, detail={"code": "RESOURCE_NOT_FOUND", "message": "Appointment not found"})
        if self.ctx.role != "PATIENT":
            self.ctx.require_hospital(appointment.hospital_id)
        record_capability(self.db, self.ctx, "get_appointment", {"appointment_id": appointment_id}, "SUCCEEDED", hospital_id=appointment.hospital_id)
        return {
            "id": appointment.id,
            "status": appointment.status,
            "starts_at": appointment.starts_at.isoformat(),
            "doctor_id": appointment.doctor_id,
            "hospital_id": appointment.hospital_id,
            "version": appointment.version,
        }

    def cancel_appointment(
        self, *, appointment_id: int, expected_version: int, idempotency_key: str,
        reason: str | None = None,
    ) -> dict:
        appointment = self.db.get(Appointment, appointment_id)
        if appointment is None or appointment.patient_id != self.ctx.patient_id:
            raise HTTPException(status_code=404, detail={"code": "RESOURCE_NOT_FOUND", "message": "Appointment not found"})
        return cancel_appointment(
            self.db, self.ctx, appointment, idempotency_key=idempotency_key,
            expected_version=expected_version, reason=reason,
        )

    def reschedule_appointment(
        self, *, appointment_id: int, starts_at: datetime, expected_version: int,
        idempotency_key: str, slot_quote_id: str, confirmation_token: str,
    ) -> dict:
        appointment = self.db.get(Appointment, appointment_id)
        if appointment is None or appointment.patient_id != self.ctx.patient_id:
            raise HTTPException(status_code=404, detail={"code": "RESOURCE_NOT_FOUND", "message": "Appointment not found"})
        return reschedule_appointment(
            self.db, self.ctx, appointment, starts_at=starts_at,
            expected_version=expected_version, idempotency_key=idempotency_key,
            slot_quote_id=slot_quote_id, confirmation_token=confirmation_token,
        )

    def list_patient_appointments(self) -> list[dict]:
        if self.ctx.patient_id is None:
            return []
        items = self.db.scalars(select(Appointment).where(
            Appointment.patient_id == self.ctx.patient_id
        ).order_by(Appointment.starts_at.desc())).all()
        return [self.get_appointment(item.id) for item in items]

    def get_questionnaire(self, assignment_id: int | None = None) -> dict | None:
        statement = select(QuestionnaireAssignment).where(
            QuestionnaireAssignment.patient_id == self.ctx.patient_id,
            QuestionnaireAssignment.status != "COMPLETED",
        )
        if assignment_id is not None:
            statement = statement.where(QuestionnaireAssignment.id == assignment_id)
        assignment = self.db.scalar(statement.order_by(QuestionnaireAssignment.created_at))
        if assignment is None:
            return None
        questionnaire = self.db.get(Questionnaire, assignment.questionnaire_id)
        questions = self.db.scalars(select(QuestionnaireQuestion).where(
            QuestionnaireQuestion.questionnaire_id == assignment.questionnaire_id
        ).order_by(QuestionnaireQuestion.position)).all()
        responses = self.db.scalars(select(QuestionnaireResponse).where(
            QuestionnaireResponse.assignment_id == assignment.id
        )).all()
        answered = {item.question_id: json.loads(item.answer_json) for item in responses}
        result = {
            "assignment_id": assignment.id,
            "title": questionnaire.title if questionnaire else "Questionnaire",
            "status": assignment.status,
            "questions": [{
                "id": item.id, "prompt": item.prompt, "type": item.question_type,
                "required": item.required, "options": json.loads(item.options_json),
                "validation": json.loads(item.validation_json),
            } for item in questions],
            "answers": answered,
        }
        record_capability(self.db, self.ctx, "get_questionnaire", {"assignment_id": assignment_id}, "SUCCEEDED")
        return result

    def submit_questionnaire_answer(
        self, *, assignment_id: int, question_id: int, value,
    ) -> dict:
        assignment = self.db.get(QuestionnaireAssignment, assignment_id)
        if assignment is None or assignment.patient_id != self.ctx.patient_id:
            raise HTTPException(status_code=404, detail={"code": "RESOURCE_NOT_FOUND", "message": "Questionnaire not found"})
        question = self.db.get(QuestionnaireQuestion, question_id)
        if question is None or question.questionnaire_id != assignment.questionnaire_id:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": "Question is not part of this questionnaire"})
        options = json.loads(question.options_json or "[]")
        rules = json.loads(question.validation_json or "{}")
        kind = question.question_type
        valid = True
        if kind == "YES_NO": valid = isinstance(value, bool)
        elif kind == "CHOICE": valid = value in options
        elif kind == "MULTIPLE_CHOICE": valid = isinstance(value, list) and all(x in options for x in value)
        elif kind == "NUMERIC":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            valid = valid and ("min" not in rules or value >= rules["min"]) and ("max" not in rules or value <= rules["max"])
        elif kind == "DATE":
            try: date.fromisoformat(str(value))
            except ValueError: valid = False
        elif kind in {"SHORT_TEXT", "LONG_TEXT"}: valid = isinstance(value, str)
        elif kind == "STRUCTURED": valid = isinstance(value, dict)
        if not valid:
            raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR", "message": f"Invalid {kind.lower()} response"})
        response = self.db.scalar(select(QuestionnaireResponse).where(
            QuestionnaireResponse.assignment_id == assignment.id,
            QuestionnaireResponse.question_id == question.id,
        ))
        if response is None:
            response = QuestionnaireResponse(assignment_id=assignment.id, question_id=question.id, answer_json="null")
            self.db.add(response)
        response.answer_json = json.dumps(value)
        all_questions = self.db.scalars(select(QuestionnaireQuestion).where(
            QuestionnaireQuestion.questionnaire_id == assignment.questionnaire_id
        )).all()
        answered_ids = set(self.db.scalars(select(QuestionnaireResponse.question_id).where(
            QuestionnaireResponse.assignment_id == assignment.id
        )).all()) | {question.id}
        complete = all(not item.required or item.id in answered_ids for item in all_questions)
        assignment.status = "COMPLETED" if complete else "IN_PROGRESS"
        record_capability(self.db, self.ctx, "submit_questionnaire", {"assignment_id": assignment_id, "question_id": question_id}, "SUCCEEDED")
        self.db.commit()
        return {"assignment_id": assignment.id, "status": assignment.status}

    def get_context(self, patient_id: int) -> dict:
        if self.ctx.patient_id != patient_id and self.ctx.role != "PLATFORM_ADMIN":
            raise HTTPException(status_code=403, detail={"code": "FORBIDDEN", "message": "Context access denied"})
        values = self.db.scalars(select(UserPreference).where(
            UserPreference.patient_id == patient_id
        )).all()
        return {item.key: __import__("json").loads(item.value_json) for item in values}
