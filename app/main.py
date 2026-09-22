from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import router, voice_websocket
from .config import settings
from .db import Base, SessionLocal, apply_lightweight_migrations, engine
from .seed import seed_demo
from .seed_showcase import seed_showcase_hospitals
from .workflows import process_due_workflows

logger = logging.getLogger("healthcare.worker")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False


async def _worker_loop() -> None:
    """Durable background poller: picks up due workflow executions (reminders,
    confirmations, reconciliation-adjacent follow-up) on a fixed interval so they run
    even if no further HTTP request ever arrives to trigger BackgroundTasks. Workflow
    state itself already lives in the database (WorkflowExecution rows with
    available_at/status), so a restart of this loop just resumes polling the same
    durable queue rather than losing anything.
    """
    while True:
        try:
            with SessionLocal() as db:
                processed = process_due_workflows(db, correlation_id=f"worker:{uuid.uuid4().hex}")
                if processed:
                    logger.info("worker processed executions: %s", processed)
        except Exception:
            logger.exception("worker loop iteration failed; will retry next tick")
        await asyncio.sleep(settings.worker_poll_interval_seconds)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    apply_lightweight_migrations()
    with SessionLocal() as db:
        seed_demo(db)
        seed_showcase_hospitals(db)
    worker_task = asyncio.create_task(_worker_loop()) if settings.worker_enabled else None
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    description="Multi-tenant healthcare discovery, scheduling, intake and AI operations platform",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    request.state.correlation_id = request.headers.get("x-correlation-id") or uuid.uuid4().hex
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["x-correlation-id"] = request.state.correlation_id
    response.headers["x-response-time-ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
    return response


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": {"code": "VALIDATION_ERROR", "message": str(exc)}})


@app.get("/")
def root():
    return {"name": settings.app_name, "status": "ready", "docs": "/docs", "health": "/api/v1/health"}


app.include_router(router)
app.add_api_websocket_route("/api/v1/voice/ws", voice_websocket)
