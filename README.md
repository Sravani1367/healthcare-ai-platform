# Healthcare AI Platform

A multi-tenant healthcare access prototype implementing the workflow in `Project_Requirements.pdf`: hospital onboarding and configuration, role-scoped dashboards, real availability, an administrative AI/voice agent, verified Mock EHR operations, structured pre-visit questionnaires, asynchronous workflows, Resend email, reconciliation, audit, and operational metrics.

## Quick start (Docker)

Prerequisites: Docker with Compose.

```bash
cp .env.example .env
# Set a strong TOKEN_SECRET. Add RESEND_API_KEY to send real email.
docker compose up --build
```

- UI: <http://localhost:8501>
- API documentation: <http://localhost:8000/docs>
- API health: <http://localhost:8000/api/v1/health>
- Mock EHR: internal Compose service `mock-ehr:9000`

Compose runs PostgreSQL, the API, Mock EHR, and Streamlit UI. Without a Resend key, email is recorded as `SIMULATED`; no SMS provider is required.

## Local development

Python 3.12 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn mock_ehr:ehr_app --reload --port 8001
uvicorn app.main:app --reload --port 8000
API_BASE_URL=http://127.0.0.1:8000/api/v1 streamlit run frontend.py
```

The local default uses SQLite. Production-style Compose uses PostgreSQL.

## Demo accounts

| Role | Email | Password |
|---|---|---|
| Platform Admin | `platform.admin@example.com` | `DemoAdmin!2026` |
| Hospital Admin | `hospital.admin@example.com` | `DemoHospital!2026` |
| Doctor | `doctor@example.com` | `DemoDoctor!2026` |
| Patient | `patient@example.com` | `DemoPatient!2026` |

Demo seeding is controlled by `ALLOW_DEMO_SEED`; disable it for a real deployment.

## Main capabilities

- Hospital registration, review, approval, suspension/reactivation, and tenant-owned configuration.
- Departments, specialties, doctors, appointment types, calendars, working hours, blocked periods, and leave.
- Patient registration, profile/preferences, appointment history, booking, verified rescheduling/cancellation, and questionnaires.
- Administrative AI with context, clarification, safe capability execution, confirmation gates, approved questionnaire collection, and urgent-language escalation.
- Browser voice using Web Speech APIs, streaming WebSocket endpoint, text fallback, interruption, silence/failure handling, and one provider-neutral inbound telephone webhook.
- Replaceable EHR connector with entity mappings, operation classification, retries, unknown-outcome search, read-after-write verification, synchronization, and reconciliation.
- Event-triggered workflows with delays, retry state, execution history, questionnaire assignment, reminders, and idempotent Resend email.
- Role dashboards, correlation IDs, audit events, operational events, traces, metrics, notifications, evaluations, and reconciliation views.

## Environment

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | SQLAlchemy database URL |
| `TOKEN_SECRET` | HMAC signing secret; must be replaced outside local development |
| `TOKEN_TTL_SECONDS` | Access token lifetime |
| `MOCK_EHR_URL` | Connector base URL |
| `ALLOW_DEMO_SEED` | Seed demo tenant and accounts |
| `RESEND_API_KEY` | Optional Resend key for real email |
| `RESEND_FROM_EMAIL` | Verified Resend sender |
| `TELEPHONE_WEBHOOK_SECRET` | Shared secret for telephone webhook calls |
| `PUBLIC_BASE_URL` | Public API base for provider callbacks |
| `API_BASE_URL` | UI-to-API URL |
| `BROWSER_API_BASE_URL` | Browser voice component's public API URL |
| `CORS_ALLOWED_ORIGINS` | Comma-separated allowed UI origins |

No key or credential is committed. The uploaded legacy `.env` is intentionally excluded from the deliverable.

## AI and voice

The runtime AI is a deterministic administrative agent for a dependable prototype. It detects supported intents and safety phrases, retains explicit conversation context, and performs actions only through `CapabilityRunner`. It does not diagnose, prescribe, change medication, or invent clinical questions. The voice UI uses browser speech recognition/synthesis; `/api/v1/voice/turn`, `/api/v1/voice/ws`, and `/api/v1/telephone/events` reuse the same agent.

See [docs/AI.md](docs/AI.md) for behavior, evaluation, and limitations.

## Mock EHR and failure recovery

The Mock EHR exposes patient, provider, facility/department, calendar, availability, appointment CRUD, search, and simulation endpoints. Set its `/ehr/simulation` flags to demonstrate a retryable failure or an unknown outcome. An unknown create response is searched before retry; if found, it is verified and synchronized without creating a duplicate. Exhausted retry paths return HTTP 202 and create a reconciliation record.

See [docs/INTEGRATION.md](docs/INTEGRATION.md).

## Workflows and Resend

Confirmed appointments enqueue an idempotent follow-up workflow. The seeded workflow assigns the approved questionnaire and sends an email confirmation. Additional supported steps include reminder email. The worker supports delay, retry/backoff, status, and execution history. Email uses Resend only; when `RESEND_API_KEY` is absent, a simulated delivery record is retained for demos/tests.

## Tests

```bash
python -m pytest -q
python -m pytest --cov=app --cov-report=term-missing
```

The acceptance suite covers authentication/RBAC, tenant isolation, availability, conflicts, idempotency, EHR mapping/create/verification, unknown-outcome recovery, reconciliation, rescheduling, cancellation, questionnaire validation and conversational collection, telephone reuse, and AI safety/clarification.

## Documentation

- [High-level design](docs/HLD.md)
- [Low-level design](docs/LLD.md)
- [Acceptance traceability](docs/ACCEPTANCE_TRACEABILITY.md)
- [AI and voice](docs/AI.md)
- [Integration and recovery](docs/INTEGRATION.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Demo script](docs/DEMO_SCRIPT.md)

## Known limitations and future improvements

- Browser speech support varies; Chrome-family browsers provide the best demo. Text fallback is always available.
- The telephone adapter is provider-neutral and requires a telephony provider to translate call audio into transcript events; SMS is intentionally not integrated.
- The deterministic prototype agent can be replaced by an LLM planner while preserving the same capability boundary and tests.
- `Base.metadata.create_all` is used for prototype setup; production should use Alembic migrations, a secrets manager, TLS termination, encrypted backups, and managed job workers.
- Public deployment, a GitHub repository, and a demo video are delivery-environment actions; the repository includes everything needed to perform them, but cannot create accounts or publish without deployment credentials.
