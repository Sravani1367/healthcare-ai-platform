# Connected Demo Script

## Successful path

1. Start the four Compose services and open the UI.
2. Sign in as Platform Admin and show hospital review, platform health and audit.
3. Sign in as Hospital Admin and show the approved hospital, doctor, appointment type, calendar, working hours, questionnaire, workflow and Mock EHR connection.
4. Sign in as Patient. Start browser voice and say: “I need someone for shoulder pain.”
5. Select the doctor, provide an available date, choose a real slot and explicitly confirm.
6. Show that the response is confirmed only after the Mock EHR create and read verification.
7. Open Appointments and show the internal/external identifier and history.
8. Ask the assistant to “complete my questionnaire”; answer the approved questions conversationally.
9. Sign in as Doctor and review the authorized structured responses.
10. Sign in as Hospital Admin and show capability, integration, workflow, notification, audit and operational metrics sharing the booking correlation trail.
11. Optionally reschedule through the assistant by naming the appointment and date, selecting a new slot and confirming. Then cancel and confirm.

## Required recovery path: unknown outcome

1. Enable Mock EHR unknown-outcome simulation.
2. Book another free slot.
3. Explain that the EHR writes the record but returns a lost-response error.
4. Show the platform searching the external state rather than blindly creating again.
5. Show one external appointment, successful read verification, synchronized internal confirmation and no duplicate.
6. Disable simulation.

## Unrecoverable path

1. Enable complete EHR failure simulation.
2. Attempt another booking.
3. Show bounded retry attempts, HTTP 202/pending state, and the reconciliation record.
4. Open Hospital Admin Operations and show the correlated failure/audit trail.
5. Resolve only after checking the external source of truth.

## Video capture checklist

Capture the connected sequence rather than isolated screens. Keep the browser console hidden, avoid displaying `.env` or secrets, show the voice turn status, and include both success and recovery paths. A 6–10 minute walkthrough is sufficient for the prototype.
