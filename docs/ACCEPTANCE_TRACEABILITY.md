# Project Requirements Acceptance Traceability

`Project_Requirements.pdf` is the governing acceptance contract. This matrix maps every numbered section to implementation and verification evidence. “External delivery” means code and instructions are present, but publication requires an account/credential outside the repository.

| § | Requirement area | Implementation evidence | Verification/status |
|---:|---|---|---|
| 1–3 | Overview, goal, principles | Modular multi-tenant platform; verified EHR before confirmation; AI safety and explicit state | Implemented; success and recovery acceptance tests |
| 4 | Users and roles | Platform Admin, Hospital Admin, Doctor, Patient in `security`, memberships and dashboards | RBAC and forbidden-dashboard test |
| 5 | Hospital onboarding | Registration, submit/review/corrections/reject/approve/suspend/reactivate APIs | Implemented; seeded approved demo plus review UI |
| 6 | Hospital/doctor configuration | Departments, specialties, doctors, types, calendars, hours, blocks/leave, lifecycle APIs | Implemented; configuration dashboard/API |
| 7 | Scheduling/availability | Time-zone conversion, hours, blocks, status gates, reservation collision and revalidation | Availability and double-booking tests |
| 8 | Patient experience | Registration, profile/preferences, history, appointments and questionnaires | Patient API/dashboard and ownership enforcement |
| 9–10 | AI, context and capabilities | Intent/clarification, discovery, booking, lookup, reschedule, cancel, questionnaire, escalation; explicit `AIContext` and `CapabilityRunner` | AI safety/clarification, questionnaire and cancellation tests |
| 11 | Voice and telephone | Browser recognition/synthesis, turn states, interruption/abort, fallback, REST/WebSocket turns, signed provider-neutral inbound events | Telephone reuse test; browser demo path |
| 12 | Mock EHR/integration | Separate Mock EHR; connector operations for lookup, availability, CRUD/retrieve/verify; identifier mappings | Mock EHR and verified booking tests |
| 13 | Verification/recovery | Response classification, bounded retry, unknown-outcome search, read verification, reconciliation | Unknown-outcome/no-duplicate and retry-exhaustion tests |
| 14 | Appointment management | Create/confirm/retrieve/reschedule/cancel/synchronize/reconcile; history and IDs | Lifecycle test verifies new slot/cancel release |
| 15 | Pre-visit questionnaire | Approved questionnaire associations, eight field types, structured responses, conversational collection, doctor review | Validation, required-answer and conversational tests |
| 16 | Workflow automation | Durable executions, triggers, delay, conditions through matching rules, retries/backoff, idempotency and history | Booking-to-workflow/notification acceptance test |
| 17 | Notifications | Configurable email records for confirmation/reminder; Resend delivery/retry/webhook; no SMS dependency | Simulated delivery test; real Resend enabled by key |
| 18 | Dashboards | Role-specific Streamlit views plus dashboard/configuration/operations APIs | Implemented for four roles |
| 19 | Analytics/observability/audit | Correlation ID, capability executions, integration operations/verifications, workflows, notifications, audit and operational events/metrics | Operations endpoints and correlated event persistence |
| 20 | AI safety | Administrative allowlist, clinical refusal, urgent-language escalation, no invented questionnaire questions | Safety-boundary test |
| 21 | Security/privacy | Scrypt passwords, signed expiring token, RBAC, tenant/resource scope, strict schemas, environment secrets, privacy-aware event metadata | Authentication and cross-tenant concealment tests |
| 22 | Data model | Explicit entities listed by the PDF, relationships, external maps, history, indexes and constraints | `app/models.py`; schema boot/test coverage |
| 23 | State management | Transactional, conversation, user, workflow, integration and operational tables separated | Documented in LLD and represented in models |
| 24 | Architecture | Interfaces → context/capabilities → core/scheduling → connector → verification → workflow/data | HLD/LLD; separate Mock EHR service |
| 25 | Reliability/idempotency | Internal and external keys, request hashes, unique reservations, verification, correlation and retry policy | Idempotency replay/misuse, collision and recovery tests |
| 26 | Testing | Unit/integration/AI/EHR/E2E-oriented acceptance suite and CI | `python -m pytest -q`; 19 tests at handoff |
| 27 | Definition of Done | Seeded hospital/doctor/calendar/integration; patient agent journey; workflow/questionnaire/email; operations | Connected demo script and automated core journey |
| 28 | Failure demonstration | Unknown-outcome recovery and unrecoverable reconciliation paths | Automated tests plus demo controls/script |
| 29.1 | Deployed application | Docker deployment assets and production checklist | External delivery: hosting account/domain required |
| 29.2 | GitHub repository | Clean source, ignore rules, CI, setup/env/tests/docs | External delivery: repository publication required |
| 29.3 | Demo video | Exact connected capture script and checklist | External delivery: recording/upload required |
| 29.4–6 | Architecture, AI docs, README | HLD, LLD, AI, integration, deployment, demo and README | Included |
| 30–31 | Technology/evaluation | Decisions justified for speed, reliability, maintainability, voice/integration/data/ops | HLD and README |
| 32–33 | Final success/product statement | Patient request is traceable through voice/AI/capability/schedule/EHR/verify/sync/workflow/doctor/admin, including recovery | Implemented and demo-ready |

## Executable acceptance criteria

The build is accepted when all of the following are true:

1. `python -m pytest -q` passes from a clean environment.
2. Hospital A cannot retrieve or mutate Hospital B private resources, even with guessed identifiers.
3. A patient can discover a provider, select a calculated slot and confirm booking; the internal appointment becomes `CONFIRMED` only after matching external retrieval.
4. Repeating the same booking key returns the same appointment; changing the payload under that key is rejected; a second request cannot reserve the same doctor/start.
5. A lost EHR response is recovered by external search and creates no duplicate.
6. An unavailable EHR exhausts its safe policy, returns HTTP 202 and creates an operations-visible reconciliation record.
7. Reschedule and cancellation validate ownership/version/state, mutate the EHR idempotently, verify it, synchronize history and reservations, and then report success.
8. The AI asks for missing/ambiguous details, uses only capabilities, requires mutation confirmation, refuses clinical decisions and escalates urgent language.
9. Browser voice and telephone transcript events reach the same agent behavior with a text fallback and failure path.
10. A confirmed booking creates the follow-up workflow, assigns only an approved matching questionnaire, records typed answers, exposes them only to authorized care staff, and emits an idempotent Resend email (or simulated email without a key).
11. Platform, hospital, doctor and patient views expose only their authorized functions/data.
12. A correlation ID traces booking across capability, appointment, integration, verification, workflow, notification and audit/operational records.
13. No secrets, local databases, caches or virtual environments are present in the submitted archive/repository.
14. Public deployment, GitHub publication and demo-video upload are completed in the owner’s chosen external accounts using the supplied deployment and demo guides.
