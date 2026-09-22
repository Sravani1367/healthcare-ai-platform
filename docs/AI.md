# AI and Voice Design

## Runtime AI

The prototype deliberately uses a deterministic administrative agent rather than an unconstrained clinical model. This makes intent tests repeatable and keeps appointment confirmations tied to verified system state. The planner can later be replaced by an LLM without changing the capability, authorization, audit, or verification boundaries.

Supported intents include hospital/doctor discovery, availability, booking, appointment lookup, rescheduling, cancellation, approved questionnaire completion, context lookup, and human escalation. The same `process_message` runtime serves text, browser voice, WebSocket, and telephone transcript channels.

## Context and resolution

Conversation context persists the current intent, hospital, doctor, slot, appointment and a small structured interaction state. Durable appointments, workflows, integration state and operational state remain in separate tables. Ambiguous doctor, appointment, date, slot or answer references produce clarification instead of guessing.

## Capability boundary

`CapabilityRunner` is the agent’s only action interface. It enforces patient ownership and tenant scope, validates arguments through underlying services, records capability executions, and delegates external mutations to the verified appointment service. Configured capability definitions describe authorization, idempotency, verification, and retry requirements.

The agent does not contain SQL for business actions and contains no vendor-specific EHR calls. It reads/writes only its own explicit conversation context; domain actions pass through capabilities.

## Safety policy

- Diagnosis, prescriptions, medication changes, treatment recommendations and independent clinical assessment are refused.
- The assistant repeats patient-provided facts without turning them into diagnoses.
- Only administrator-approved questionnaire questions are presented.
- Predefined urgent phrases create a human escalation and direct the patient to emergency/local urgent care.
- Create, cancellation and reschedule actions use confirmation gates.
- A booking is never described as confirmed until the EHR record is retrieved and matched.

## Voice technology

- Browser speech recognition and speech synthesis provide the demo audio channel.
- Interim recognition output, turn-state feedback, explicit stop/barge-in, request abort, silence/failure messages, and text fallback are included.
- `/api/v1/voice/ws` provides a low-overhead persistent turn channel.
- `/api/v1/telephone/events` accepts authenticated inbound provider events, identifies a patient by verified email supplied by the adapter, and routes transcripts through the same runtime.
- A production telephony adapter should perform provider signature verification, streaming STT/TTS, caller verification and live-agent transfer. No SMS provider is used.

## Prompts and decision rules

The important runtime rules are encoded as testable policies rather than a hidden system prompt:

1. Administrative scope only.
2. Clarify missing/ambiguous identifiers.
3. Use active conversation context for follow-up turns.
4. Offer only calculated availability.
5. Confirm before mutation.
6. Execute through a named capability.
7. Report the verified state, not an assumed state.
8. Escalate urgent or unresolved cases.

## Evaluation

Automated tests cover safety refusal, discovery clarification, capability-driven booking, telephone reuse, conversational questionnaire collection, confirmation-gated cancellation, idempotency, verification and unknown-outcome recovery. `ai_evaluations` stores named pass/fail/score records for demo or offline evaluation. Useful production additions are paraphrase suites, multilingual evaluation, provider-specific voice latency, hallucination checks, and human-reviewed transcripts with PHI controls.
