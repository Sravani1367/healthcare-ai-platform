from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    Appointment,
    Doctor,
    Patient,
    Questionnaire,
    QuestionnaireAssignment,
    User,
    Workflow,
    WorkflowExecution,
    utcnow,
)
from .notifications import deliver_email, queue_email
from .telemetry import record_event


def enqueue_workflows(
    db: Session,
    *,
    trigger_event: str,
    appointment: Appointment,
    correlation_id: str,
) -> list[WorkflowExecution]:
    workflows = db.scalars(select(Workflow).where(
        Workflow.hospital_id == appointment.hospital_id,
        Workflow.trigger_event == trigger_event,
        Workflow.status == "ACTIVE",
    )).all()
    executions: list[WorkflowExecution] = []
    for workflow in workflows:
        event_key = f"{trigger_event}:{appointment.id}"
        existing = db.scalar(select(WorkflowExecution).where(
            WorkflowExecution.workflow_id == workflow.id,
            WorkflowExecution.event_key == event_key,
        ))
        if existing:
            executions.append(existing)
            continue
        definition = json.loads(workflow.definition_json or "{}")
        delay = max(0, min(int(definition.get("delay_seconds", 0)), 2_592_000))
        execution = WorkflowExecution(
            workflow_id=workflow.id,
            appointment_id=appointment.id,
            event_key=event_key,
            status="PENDING" if delay == 0 else "WAITING",
            current_step="queued",
            available_at=utcnow() + timedelta(seconds=delay),
        )
        db.add(execution)
        db.flush()
        executions.append(execution)
        record_event(
            db, correlation_id, "WorkflowQueued", "SUCCEEDED",
            hospital_id=appointment.hospital_id,
            resource_type="workflow_execution", resource_id=execution.id,
            metadata={"trigger": trigger_event},
        )
    return executions


# SF-11: a notification tied to an appointment that is no longer in an active/eligible
# state must never send, even if it was already queued before the state changed.
_NON_NOTIFIABLE_STATUSES = {"CANCELLED", "FAILED"}


def cancel_pending_workflows(
    db: Session, *, appointment: Appointment, correlation_id: str,
) -> None:
    """Stop not-yet-run workflow executions for this appointment (e.g. a queued
    reminder) from firing after cancellation. Already-COMPLETED executions (an email
    that already sent) are left as history; RUNNING executions are not touched here
    since the same-transaction status flip and the _NON_NOTIFIABLE_STATUSES check in
    process_workflow cover that race.
    """
    executions = db.scalars(select(WorkflowExecution).where(
        WorkflowExecution.appointment_id == appointment.id,
        WorkflowExecution.status.in_(["PENDING", "WAITING", "RETRYING"]),
    )).all()
    for execution in executions:
        execution.status = "CANCELLED"
        execution.error_message = f"Superseded: appointment status is now {appointment.status}"
        record_event(
            db, correlation_id, "WorkflowExecution", "CANCELLED",
            hospital_id=appointment.hospital_id,
            resource_type="workflow_execution", resource_id=execution.id,
            metadata={"reason": "appointment_status_changed", "appointment_status": appointment.status},
        )


# D6: exactly one published questionnaire may be assigned per appointment, chosen by
# the most specific selector that exactly matches. Ties at the same priority level are
# a publish-time configuration error and are rejected rather than assigned arbitrarily.
_SELECTOR_PRIORITY = [
    ("doctor_id", "appointment_type_id"),
    ("doctor_id", None),
    ("specialty_id", "appointment_type_id"),
    ("specialty_id", None),
    (None, "appointment_type_id"),
    (None, None),
]


def _select_questionnaire(
    questionnaires: list[Questionnaire], appointment: Appointment, specialty_id: int | None,
) -> Questionnaire | None:
    active = [q for q in questionnaires if q.status == "ACTIVE"]
    for scope_field, type_field in _SELECTOR_PRIORITY:
        candidates = []
        for q in active:
            if scope_field == "doctor_id" and q.doctor_id != appointment.doctor_id:
                continue
            if scope_field == "specialty_id" and (q.specialty_id is None or q.specialty_id != specialty_id):
                continue
            if scope_field is None and (q.doctor_id is not None or q.specialty_id is not None):
                continue
            if type_field == "appointment_type_id" and q.appointment_type_id != appointment.appointment_type_id:
                continue
            if type_field is None and q.appointment_type_id is not None:
                continue
            candidates.append(q)
        if len(candidates) > 1:
            raise ValueError(
                f"Questionnaire selector collision: {len(candidates)} active questionnaires "
                f"tie at priority (scope={scope_field}, type={type_field}) for appointment {appointment.id}"
            )
        if candidates:
            return candidates[0]
    return None


def process_workflow(
    db: Session, execution: WorkflowExecution, correlation_id: str
) -> WorkflowExecution:
    workflow = db.get(Workflow, execution.workflow_id)
    appointment = db.get(Appointment, execution.appointment_id)
    if workflow is None or appointment is None:
        execution.status = "FAILED"
        execution.error_message = "Workflow or appointment not found"
        return execution
    if execution.status == "COMPLETED":
        return execution
    execution.status = "RUNNING"
    execution.attempts += 1
    definition = json.loads(workflow.definition_json or "{}")
    try:
        steps = definition.get("steps") or ["ASSIGN_QUESTIONNAIRE", "SEND_CONFIRMATION_EMAIL"]
        for step in steps:
            execution.current_step = step
            if step == "ASSIGN_QUESTIONNAIRE":
                already_assigned = db.scalar(select(QuestionnaireAssignment).where(
                    QuestionnaireAssignment.appointment_id == appointment.id
                ))
                if already_assigned is None:
                    questionnaires = db.scalars(select(Questionnaire).where(
                        Questionnaire.hospital_id == appointment.hospital_id
                    )).all()
                    doctor = db.get(Doctor, appointment.doctor_id)
                    try:
                        selected = _select_questionnaire(questionnaires, appointment, doctor.specialty_id if doctor else None)
                    except ValueError as exc:
                        # A selector collision is a hospital configuration error, not a
                        # reason to also block confirmation email or other steps in this
                        # workflow — record it and continue.
                        record_event(
                            db, correlation_id, "QuestionnaireSelectorCollision", "FAILED",
                            hospital_id=appointment.hospital_id,
                            resource_type="appointment", resource_id=appointment.id,
                            metadata={"detail": str(exc)},
                        )
                        selected = None
                    if selected is not None:
                        db.add(QuestionnaireAssignment(
                            appointment_id=appointment.id,
                            questionnaire_id=selected.id,
                            patient_id=appointment.patient_id,
                        ))
            elif step == "SEND_CONFIRMATION_EMAIL":
                if appointment.status in _NON_NOTIFIABLE_STATUSES:
                    record_event(
                        db, correlation_id, "NotificationSkippedStaleState", "SKIPPED",
                        hospital_id=appointment.hospital_id,
                        resource_type="appointment", resource_id=appointment.id,
                        metadata={"step": step, "appointment_status": appointment.status},
                    )
                    continue
                patient = db.get(Patient, appointment.patient_id)
                user = db.get(User, patient.user_id) if patient else None
                if patient and user:
                    notification = queue_email(
                        db,
                        hospital_id=appointment.hospital_id,
                        appointment_id=appointment.id,
                        recipient_user_id=user.id,
                        recipient=patient.email,
                        notification_type="APPOINTMENT_CONFIRMATION",
                        template_data={
                            "subject": "Your appointment is confirmed",
                            "title": "Appointment confirmed",
                            "lines": [
                                f"Appointment ID: {appointment.id}",
                                f"Starts: {appointment.starts_at.isoformat()} UTC",
                            ],
                        },
                        idempotency_key=f"workflow:{execution.id}:confirmation:v1",
                    )
                    deliver_email(db, notification, correlation_id)
            elif step == "SEND_REMINDER_EMAIL":
                if appointment.status in _NON_NOTIFIABLE_STATUSES:
                    record_event(
                        db, correlation_id, "NotificationSkippedStaleState", "SKIPPED",
                        hospital_id=appointment.hospital_id,
                        resource_type="appointment", resource_id=appointment.id,
                        metadata={"step": step, "appointment_status": appointment.status},
                    )
                    continue
                patient = db.get(Patient, appointment.patient_id)
                user = db.get(User, patient.user_id) if patient else None
                if patient and user:
                    notification = queue_email(
                        db,
                        hospital_id=appointment.hospital_id,
                        appointment_id=appointment.id,
                        recipient_user_id=user.id,
                        recipient=patient.email,
                        notification_type="APPOINTMENT_REMINDER",
                        template_data={
                            "subject": "Appointment reminder",
                            "title": "Upcoming appointment",
                            "lines": [f"Starts: {appointment.starts_at.isoformat()} UTC"],
                        },
                        idempotency_key=f"workflow:{execution.id}:reminder:v1",
                    )
                    deliver_email(db, notification, correlation_id)
            else:
                raise ValueError(f"Unsupported workflow step: {step}")
        execution.status = "COMPLETED"
        execution.completed_at = utcnow()
        execution.error_message = None
    except Exception as exc:
        execution.error_message = str(exc)[:500]
        if execution.attempts < int(definition.get("max_attempts", 3)):
            execution.status = "RETRYING"
            execution.available_at = utcnow() + timedelta(seconds=2**execution.attempts)
        else:
            execution.status = "FAILED"
    record_event(
        db, correlation_id, "WorkflowExecution", execution.status,
        hospital_id=appointment.hospital_id,
        resource_type="workflow_execution", resource_id=execution.id,
        metadata={"attempts": execution.attempts, "step": execution.current_step},
    )
    return execution


def process_due_workflows(db: Session, correlation_id: str, limit: int = 50) -> list[int]:
    now = utcnow()
    executions = db.scalars(select(WorkflowExecution).where(
        WorkflowExecution.status.in_(["PENDING", "WAITING", "RETRYING"]),
        WorkflowExecution.available_at <= now,
    ).limit(limit)).all()
    processed = []
    for execution in executions:
        process_workflow(db, execution, correlation_id)
        processed.append(execution.id)
    db.commit()
    return processed

