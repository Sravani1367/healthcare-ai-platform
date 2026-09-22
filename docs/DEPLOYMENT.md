# Deployment Guide

## Reproducible deployment

The included Dockerfile and Compose file are the reference deployment. For a public environment, deploy the `api`, `frontend` and `mock-ehr` services behind TLS and use managed PostgreSQL.

```bash
cp .env.example .env
# Generate and set a long random TOKEN_SECRET.
# Set RESEND_API_KEY and a verified RESEND_FROM_EMAIL if real mail is required.
docker compose up --build -d
docker compose ps
curl http://localhost:8000/api/v1/health
```

## Production checklist

- Set `APP_ENV=production`, a high-entropy `TOKEN_SECRET`, the managed `DATABASE_URL`, correct public URLs, and `ALLOW_DEMO_SEED=false` after creating real administrators.
- Store secrets in the hosting platform’s secret manager; never bake `.env` into an image.
- Use TLS at the load balancer and encrypted PostgreSQL connections/backups.
- Configure a verified Resend domain/sender and webhook route `/api/v1/webhooks/resend`.
- Configure the telephone adapter to sign `/api/v1/telephone/events` requests with `x-telephone-webhook-secret`.
- Restrict CORS to the deployed UI origin.
- Run database migrations rather than `create_all` for long-lived production evolution.
- Run workflow processing as an independently supervised worker/scheduler.
- Add centralized logs/metrics with PHI redaction, retention policies and alerting.
- Run the test suite and a post-deploy booking/recovery smoke test.

## GitHub repository and CI

Initialize a repository only after confirming `.env`, database files, caches and local virtual environments are ignored. The supplied GitHub Actions workflow runs compilation and tests on Python 3.12. Configure deployment credentials only as repository/environment secrets.

## Scaling

The stateless API and UI can scale horizontally. PostgreSQL uniqueness constraints remain the booking collision authority. External operations already carry idempotency keys. For higher throughput, move workflow execution to a queue with leased jobs and use a distributed rate limiter for EHR and email adapters.

## Rollback and recovery

Use immutable image versions. Roll back application images independently of the database; schema migrations need explicit backward-compatible rollback plans. Never resolve uncertain EHR writes by deleting internal records—use reconciliation, inspect the external source of truth, and record the operator decision.
