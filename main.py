"""Compatibility entry point: run with ``uvicorn main:app --reload``."""

from app.main import app


__all__ = ["app"]

