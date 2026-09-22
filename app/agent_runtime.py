from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .capabilities import CapabilityRunner
from .models import (
    AIContext,
    AIConversation,
    Appointment,
    AppointmentType,
    Doctor,
    HumanEscalation,
)
from .security import RequestContext
from .telemetry import record_event


CLINICAL_REQUEST = re.compile(
    r"\b(diagnos|prescrib|medication|medicine|treatment|what do i have|should i take)\b",
    re.IGNORECASE,
)
URGENT_REPORT = re.compile(
    r"\b(chest pain|chest discomfort|cannot breathe|can't breathe|severe bleeding|unconscious)\b",
    re.IGNORECASE,
)

SPECIALTY_HINTS = {
    "shoulder": "Orthopedics",
    "knee": "Orthopedics",
    "bone": "Orthopedics",
    "skin": "Dermatology",
    "rash": "Dermatology",
    "heart": "Cardiology",
    "child": "Pediatrics",
    "pediatric": "Pediatrics",
}


def _date_hint(message: str) -> date | None:
    lowered = message.lower()
    today = date.today()
    if "tomorrow" in lowered:
        return today + timedelta(days=1)
    if "today" in lowered:
        return today
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    for name, number in weekdays.items():
        if name in lowered:
            delta = (number - today.weekday()) % 7
            return today + timedelta(days=delta or 7)
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", message)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None
    return None


def _specialty_hint(message: str) -> str | None:
    lowered = message.lower()
    for hint, specialty in SPECIALTY_HINTS.items():
        if hint in lowered:
            return specialty
    direct = re.search(r"\b(orthopedics|dermatology|cardiology|pediatrics)\b", lowered)
    return direct.group(1).title() if direct else None


def _appointment_id_hint(message: str) -> int | None:
    match = re.search(r"(?:appointment\s*#?|#)(\d+)", message, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _questionnaire_value(message: str, question: dict):
    value = message.strip()
    kind = question["type"]
    if kind == "YES_NO":
        if value.lower() in {"yes", "y", "true"}: return True
        if value.lower() in {"no", "n", "false"}: return False
        raise ValueError("Please answer yes or no.")
    if kind == "NUMERIC":
        try: return float(value) if "." in value else int(value)
        except ValueError as exc: raise ValueError("Please provide a number.") from exc
    if kind == "DATE":
        parsed = _date_hint(value)
        if parsed is None:
            try: parsed = date.fromisoformat(value)
            except ValueError as exc: raise ValueError("Please use YYYY-MM-DD.") from exc
        return parsed.isoformat()
    if kind == "CHOICE":
        options = question.get("options", [])
        choice = re.fullmatch(r"(?:option\s*)?(\d+)", value.lower())
        if choice and 0 < int(choice.group(1)) <= len(options):
            return options[int(choice.group(1)) - 1]
        for option in options:
            if value.casefold() == str(option).casefold(): return option
        raise ValueError("Please choose one of the listed options.")
    if kind == "MULTIPLE_CHOICE":
        requested = [part.strip() for part in value.split(",") if part.strip()]
        options = question.get("options", [])
        matched = [option for option in options if str(option).casefold() in {x.casefold() for x in requested}]
        if len(matched) != len(requested): raise ValueError("Please list valid choices separated by commas.")
        return matched
    if kind == "STRUCTURED":
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("Please provide the structured answer as JSON.") from exc
        if not isinstance(parsed, dict): raise ValueError("The structured answer must be an object.")
        return parsed
    return value


def _question_prompt(question: dict) -> str:
    options = question.get("options") or []
    suffix = ""
    if options:
        suffix = " Options: " + "; ".join(f"{i + 1}. {item}" for i, item in enumerate(options))
    return f"{question['prompt']}{suffix}"


def _context_datetime(value: datetime) -> datetime:
    """SQLite drops timezone metadata; conversational slot values are stored as UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _conversation(
    db: Session, ctx: RequestContext, conversation_id: str | None
) -> tuple[AIConversation, AIContext]:
    if conversation_id:
        conversation = db.get(AIConversation, conversation_id)
        if conversation and conversation.patient_id != ctx.patient_id:
            conversation = None
    else:
        conversation = None
    if conversation is None:
        conversation = AIConversation(
            id=conversation_id or uuid.uuid4().hex,
            patient_id=ctx.patient_id,
            status="ACTIVE",
        )
        db.add(conversation)
        db.flush()
        state = AIContext(conversation_id=conversation.id)
        db.add(state)
        db.flush()
    else:
        state = db.get(AIContext, conversation.id)
    return conversation, state


def process_message(
    db: Session,
    ctx: RequestContext,
    *,
    message: str,
    conversation_id: str | None,
) -> dict:
    if ctx.patient_id is None:
        return {
            "conversation_id": conversation_id,
            "text": "The patient assistant is available only to registered patient accounts.",
            "status": "FORBIDDEN",
        }
    conversation, state = _conversation(db, ctx, conversation_id)
    runner = CapabilityRunner(db, ctx)
    lowered = message.lower().strip()
    record_event(
        db, ctx.correlation_id, "AIConversationTurn", "STARTED",
        hospital_id=conversation.hospital_id,
        resource_type="conversation", resource_id=conversation.id,
        metadata={"message_length": len(message)},
    )

    if URGENT_REPORT.search(message):
        db.add(HumanEscalation(
            hospital_id=state.selected_hospital_id,
            patient_id=ctx.patient_id,
            conversation_id=conversation.id,
            reason_code="POTENTIAL_URGENCY",
            summary="Predefined urgent-language policy matched. Review required.",
        ))
        state.current_intent = "HUMAN_ESCALATION"
        text = (
            "I cannot assess or diagnose symptoms. Your message may need urgent attention. "
            "Please contact local emergency services or seek immediate medical care. "
            "I have also created a human follow-up escalation."
        )
        db.commit()
        return {"conversation_id": conversation.id, "text": text, "status": "ESCALATED"}

    if CLINICAL_REQUEST.search(message):
        text = (
            "I can help with hospitals, doctors, appointments, and approved pre-visit "
            "questions, but I cannot diagnose, prescribe, change medication, or recommend treatment."
        )
        db.commit()
        return {"conversation_id": conversation.id, "text": text, "status": "SAFETY_BOUNDARY"}

    stored = json.loads(state.state_json or "{}")

    if state.current_intent == "COMPLETE_QUESTIONNAIRE" and stored.get("questionnaire"):
        questionnaire = runner.get_questionnaire(stored["questionnaire"]["assignment_id"])
        if questionnaire is None:
            state.current_intent = None
            state.state_json = "{}"
            db.commit()
            return {"conversation_id": conversation.id, "text": "That questionnaire is no longer pending."}
        unanswered = [q for q in questionnaire["questions"] if q["id"] not in questionnaire["answers"]]
        if not unanswered:
            state.current_intent = None
            state.state_json = "{}"
            db.commit()
            return {"conversation_id": conversation.id, "text": "Your questionnaire is complete and available to the care team."}
        question = unanswered[0]
        try:
            value = _questionnaire_value(message, question)
            result = runner.submit_questionnaire_answer(
                assignment_id=questionnaire["assignment_id"], question_id=question["id"], value=value,
            )
        except (ValueError, TypeError) as exc:
            db.commit()
            return {"conversation_id": conversation.id, "text": f"{exc} {_question_prompt(question)}", "needs_clarification": True}
        if result["status"] == "COMPLETED":
            state.current_intent = None
            state.state_json = "{}"
            db.commit()
            return {"conversation_id": conversation.id, "text": "Thank you. Your structured questionnaire is complete and ready for authorized review."}
        questionnaire = runner.get_questionnaire(questionnaire["assignment_id"])
        next_question = next(q for q in questionnaire["questions"] if q["id"] not in questionnaire["answers"])
        db.commit()
        return {"conversation_id": conversation.id, "text": _question_prompt(next_question)}

    if "questionnaire" in lowered or "pre-visit questions" in lowered:
        questionnaire = runner.get_questionnaire()
        if questionnaire is None:
            db.commit()
            return {"conversation_id": conversation.id, "text": "You do not have a pending questionnaire."}
        unanswered = [q for q in questionnaire["questions"] if q["id"] not in questionnaire["answers"]]
        if not unanswered:
            db.commit()
            return {"conversation_id": conversation.id, "text": "Your questionnaire is already complete."}
        state.current_intent = "COMPLETE_QUESTIONNAIRE"
        stored["questionnaire"] = {"assignment_id": questionnaire["assignment_id"]}
        state.state_json = json.dumps(stored)
        db.commit()
        return {"conversation_id": conversation.id, "text": f"Let's complete {questionnaire['title']}. {_question_prompt(unanswered[0])}"}

    if lowered in {"confirm cancel", "yes, cancel", "cancel it"} and stored.get("awaiting_cancel_confirmation"):
        appointment = runner.get_appointment(state.current_appointment_id)
        result = runner.cancel_appointment(
            appointment_id=appointment["id"], expected_version=appointment["version"],
            idempotency_key=f"conversation:{conversation.id}:cancel:{appointment['id']}",
            reason="Patient requested through AI assistant",
        )
        state.current_intent = None
        state.state_json = "{}"
        db.commit()
        return {"conversation_id": conversation.id, "text": f"Appointment #{result['id']} is cancelled and the external record was verified.", "appointment": result}

    if "cancel" in lowered and "appointment" in lowered:
        appointments = [x for x in runner.list_patient_appointments() if x["status"] in {"CONFIRMED", "RESCHEDULED"}]
        requested_id = _appointment_id_hint(message)
        matches = [x for x in appointments if x["id"] == requested_id] if requested_id else appointments
        if not matches:
            db.commit()
            return {"conversation_id": conversation.id, "text": "I could not find an active appointment matching that request."}
        if len(matches) > 1:
            db.commit()
            return {"conversation_id": conversation.id, "text": "Which appointment should I cancel? " + "; ".join(f"#{x['id']} on {x['starts_at']}" for x in matches), "needs_clarification": True}
        state.current_intent = "CANCEL_APPOINTMENT"
        state.current_appointment_id = matches[0]["id"]
        stored["awaiting_cancel_confirmation"] = True
        state.state_json = json.dumps(stored)
        db.commit()
        return {"conversation_id": conversation.id, "text": f"Please confirm cancellation of appointment #{matches[0]['id']} on {matches[0]['starts_at']} by saying confirm cancel.", "needs_confirmation": True}

    if lowered in {"confirm reschedule", "yes, reschedule", "move it"} and stored.get("awaiting_reschedule_confirmation"):
        quote_id = stored.get("selected_slot_quote_id")
        if not quote_id:
            db.commit()
            return {"conversation_id": conversation.id, "text": "That quote has expired. Let's check availability again — which date would you like?", "needs_clarification": True}
        appointment = runner.get_appointment(state.current_appointment_id)
        result = runner.reschedule_appointment(
            appointment_id=appointment["id"], starts_at=_context_datetime(state.selected_start),
            expected_version=appointment["version"],
            idempotency_key=f"conversation:{conversation.id}:reschedule:{appointment['id']}:{state.selected_start.isoformat()}",
            slot_quote_id=quote_id, confirmation_token=quote_id,
        )
        state.current_intent = None
        state.state_json = "{}"
        db.commit()
        return {"conversation_id": conversation.id, "text": f"Appointment #{result['id']} was rescheduled and externally verified for {result['starts_at']}.", "appointment": result}

    if "reschedul" in lowered or ("move" in lowered and "appointment" in lowered):
        appointments = [x for x in runner.list_patient_appointments() if x["status"] in {"CONFIRMED", "RESCHEDULED"}]
        requested_id = _appointment_id_hint(message)
        matches = [x for x in appointments if x["id"] == requested_id] if requested_id else appointments
        if len(matches) != 1:
            db.commit()
            text = "I could not find an active appointment." if not matches else "Which appointment should I reschedule? " + "; ".join(f"#{x['id']} on {x['starts_at']}" for x in matches)
            return {"conversation_id": conversation.id, "text": text, "needs_clarification": bool(matches)}
        requested_date = _date_hint(message)
        if requested_date is None:
            db.commit()
            return {"conversation_id": conversation.id, "text": "What new date would you prefer? Include the appointment number if you have more than one.", "needs_clarification": True}
        appointment = matches[0]
        appointment_record = db.get(Appointment, appointment["id"])
        slots = runner.check_availability(
            doctor_id=appointment["doctor_id"], appointment_type_id=appointment_record.appointment_type_id,
            date_from=requested_date, date_to=requested_date,
        )
        if not slots:
            db.commit()
            return {"conversation_id": conversation.id, "text": "No valid slots are available that day. Please choose another date."}
        state.current_intent = "RESCHEDULE_APPOINTMENT"
        state.current_appointment_id = appointment["id"]
        stored = {"candidate_slots": slots[:5], "slot_action": "RESCHEDULE"}
        state.state_json = json.dumps(stored)
        db.commit()
        return {"conversation_id": conversation.id, "text": "New slots: " + "; ".join(f"{i + 1}. {x['starts_at']}" for i, x in enumerate(slots[:5])) + ". Reply with the option number.", "options": slots[:5]}

    if "appointment" in lowered and any(word in lowered for word in ["my", "list", "show", "upcoming"]):
        appointments = runner.list_patient_appointments()
        db.commit()
        if not appointments:
            text = "You do not have any appointments yet."
        else:
            text = "Your appointments are: " + "; ".join(
                f"#{x['id']} on {x['starts_at']} ({x['status']})" for x in appointments[:5]
            )
        return {"conversation_id": conversation.id, "text": text, "appointments": appointments}

    specialty = _specialty_hint(message)
    if specialty:
        state.current_intent = "BOOK_APPOINTMENT"
        doctors = runner.search_doctors(specialty=specialty)
        state.state_json = json.dumps({"candidate_doctors": [x["id"] for x in doctors]})
        db.commit()
        if not doctors:
            return {"conversation_id": conversation.id, "text": f"I could not find an active {specialty} doctor."}
        if len(doctors) > 1:
            choices = "; ".join(
                f"{index + 1}. {item['name']} at {item['hospital']}"
                for index, item in enumerate(doctors[:5])
            )
            return {
                "conversation_id": conversation.id,
                "text": f"I found multiple options. Which doctor do you prefer? {choices}",
                "options": doctors[:5],
                "needs_clarification": True,
            }
        state.selected_doctor_id = doctors[0]["id"]
        state.selected_hospital_id = doctors[0]["hospital_id"]

    stored = json.loads(state.state_json or "{}")
    candidates = stored.get("candidate_doctors", [])
    selection = re.fullmatch(r"(?:option\s*)?(\d+)", lowered)
    if selection and candidates:
        index = int(selection.group(1)) - 1
        if 0 <= index < len(candidates):
            doctor = db.get(Doctor, candidates[index])
            state.selected_doctor_id = doctor.id
            state.selected_hospital_id = doctor.hospital_id

    if (
        state.current_intent == "BOOK_APPOINTMENT"
        and state.selected_doctor_id
        and not stored.get("candidate_slots")
    ):
        requested_date = _date_hint(message)
        if requested_date is None:
            db.commit()
            return {
                "conversation_id": conversation.id,
                "text": "Which date would you prefer? You can say tomorrow, Friday, or YYYY-MM-DD.",
                "needs_clarification": True,
            }
        appointment_types = db.scalars(select(AppointmentType).where(
            AppointmentType.hospital_id == state.selected_hospital_id,
            AppointmentType.status == "ACTIVE",
        )).all()
        if not appointment_types:
            db.commit()
            return {"conversation_id": conversation.id, "text": "This hospital has no active appointment type configured."}
        slots = runner.check_availability(
            doctor_id=state.selected_doctor_id,
            appointment_type_id=appointment_types[0].id,
            date_from=requested_date,
            date_to=requested_date,
        )
        stored.update({
            "candidate_slots": slots[:5],
            "appointment_type_id": appointment_types[0].id,
        })
        state.state_json = json.dumps(stored)
        db.commit()
        if not slots:
            return {"conversation_id": conversation.id, "text": "No valid slots are available on that date. Would you like another date?"}
        text = "Available slots: " + "; ".join(
            f"{i + 1}. {slot['starts_at']}" for i, slot in enumerate(slots[:5])
        ) + ". Reply with the option number; I will ask for confirmation before booking."
        return {"conversation_id": conversation.id, "text": text, "options": slots[:5]}

    slot_selection = re.fullmatch(r"(?:slot|option)?\s*(\d+)", lowered)
    candidate_slots = stored.get("candidate_slots", [])
    if slot_selection and candidate_slots:
        index = int(slot_selection.group(1)) - 1
        if 0 <= index < len(candidate_slots):
            state.selected_start = datetime.fromisoformat(
                candidate_slots[index]["starts_at"].replace("Z", "+00:00")
            )
            stored["selected_slot_quote_id"] = candidate_slots[index].get("slot_quote_id")
            if stored.get("slot_action") == "RESCHEDULE":
                stored["awaiting_reschedule_confirmation"] = True
                prompt = f"Please confirm moving appointment #{state.current_appointment_id} to {candidate_slots[index]['starts_at']} by replying confirm reschedule."
            else:
                stored["awaiting_confirmation"] = True
                prompt = f"Please confirm booking the slot at {candidate_slots[index]['starts_at']} by replying confirm."
            state.state_json = json.dumps(stored)
            db.commit()
            return {
                "conversation_id": conversation.id,
                "text": prompt,
                "needs_confirmation": True,
            }

    if lowered in {"confirm", "yes, confirm", "book it"} and stored.get("awaiting_confirmation"):
        quote_id = stored.get("selected_slot_quote_id")
        if not quote_id:
            db.commit()
            return {"conversation_id": conversation.id, "text": "That quote has expired. Let's check availability again — which date would you like?", "needs_clarification": True}
        result = runner.create_appointment(
            doctor_id=state.selected_doctor_id,
            appointment_type_id=stored["appointment_type_id"],
            starts_at=_context_datetime(state.selected_start),
            idempotency_key=f"conversation:{conversation.id}:{_context_datetime(state.selected_start).isoformat()}",
            slot_quote_id=quote_id,
            confirmation_token=quote_id,
        )
        state.current_appointment_id = result["id"]
        stored["awaiting_confirmation"] = False
        state.state_json = json.dumps(stored)
        db.commit()
        if result["status"] == "CONFIRMED":
            text = f"Your appointment #{result['id']} is confirmed after external verification."
        else:
            text = f"Your appointment request #{result['id']} is pending verification. I will not call it confirmed yet."
        return {"conversation_id": conversation.id, "text": text, "appointment": result}

    hospitals = runner.search_hospitals()
    db.commit()
    return {
        "conversation_id": conversation.id,
        "text": (
            "I can help you find a hospital or doctor, check real availability, book, "
            "reschedule or cancel an appointment, and complete approved questionnaires. "
            "What administrative task would you like help with?"
        ),
        "hospitals": hospitals[:5],
        "needs_clarification": True,
    }
