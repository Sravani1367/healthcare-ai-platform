from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Healthcare AI Platform")
    environment: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./healthcare_v2.db")
    token_secret: str = os.getenv("TOKEN_SECRET", "development-only-change-me")
    token_ttl_seconds: int = int(os.getenv("TOKEN_TTL_SECONDS", "28800"))
    mock_ehr_url: str = os.getenv("MOCK_EHR_URL", "http://127.0.0.1:8001")
    resend_api_key: str | None = os.getenv("RESEND_API_KEY")
    resend_from_email: str = os.getenv(
        "RESEND_FROM_EMAIL", "Healthcare Platform <onboarding@resend.dev>"
    )
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
    cors_allowed_origins: tuple[str, ...] = tuple(
        value.strip() for value in os.getenv(
            "CORS_ALLOWED_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501"
        ).split(",") if value.strip()
    )
    telephone_provider: str = os.getenv("TELEPHONE_PROVIDER", "webhook")
    telephone_webhook_secret: str | None = os.getenv("TELEPHONE_WEBHOOK_SECRET")
    allow_demo_seed: bool = os.getenv("ALLOW_DEMO_SEED", "true").lower() == "true"
    worker_poll_interval_seconds: float = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5"))
    worker_enabled: bool = os.getenv("WORKER_ENABLED", "true").lower() == "true"


settings = Settings()
