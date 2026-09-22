from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


connect_args = (
    {"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(settings.database_url, connect_args=connect_args, future=True)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_integrity(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def apply_lightweight_migrations() -> None:
    """Add columns introduced after the table was first created.

    This project has no migration framework; Base.metadata.create_all only
    creates missing tables, not missing columns on existing ones. This keeps
    existing local/demo SQLite databases working across model changes.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(doctors)").fetchall()}
        if not existing:
            return
        statements = []
        if "reviewed_by" not in existing:
            statements.append("ALTER TABLE doctors ADD COLUMN reviewed_by INTEGER")
        if "reviewed_at" not in existing:
            statements.append("ALTER TABLE doctors ADD COLUMN reviewed_at DATETIME")
        if "review_notes" not in existing:
            statements.append("ALTER TABLE doctors ADD COLUMN review_notes TEXT")
        for statement in statements:
            conn.exec_driver_sql(statement)
        if statements:
            conn.commit()
        if "status" in existing:
            conn.exec_driver_sql(
                "UPDATE doctors SET status = 'PENDING_APPROVAL' WHERE status = 'INVITED'"
            )
            conn.commit()
        _dedupe_calendars(conn)

        questionnaire_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(questionnaires)").fetchall()}
        if questionnaire_columns:
            q_statements = []
            if "proposed_by_user_id" not in questionnaire_columns:
                q_statements.append("ALTER TABLE questionnaires ADD COLUMN proposed_by_user_id INTEGER")
            if "published_by_user_id" not in questionnaire_columns:
                q_statements.append("ALTER TABLE questionnaires ADD COLUMN published_by_user_id INTEGER")
            if "published_at" not in questionnaire_columns:
                q_statements.append("ALTER TABLE questionnaires ADD COLUMN published_at DATETIME")
            for statement in q_statements:
                conn.exec_driver_sql(statement)
            if q_statements:
                conn.commit()
            # Pre-existing questionnaires were created directly as ACTIVE by hospital
            # admins before this migration; treat them as already published so they
            # keep working without requiring a retroactive publish step.
            conn.exec_driver_sql(
                "UPDATE questionnaires SET published_at = created_at WHERE status = 'ACTIVE' AND published_at IS NULL"
            )
            conn.commit()


def _dedupe_calendars(conn) -> None:
    """Merge duplicate (doctor_id, appointment_type_id) calendars into one before the
    unique constraint is enforced going forward, re-pointing any working hours, blocked
    periods, and appointments that referenced a duplicate onto the surviving calendar.
    """
    tables = {row[0] for row in conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='calendars'"
    ).fetchall()}
    if not tables:
        return
    groups = conn.exec_driver_sql(
        "SELECT doctor_id, appointment_type_id, COUNT(*) c FROM calendars "
        "GROUP BY doctor_id, appointment_type_id HAVING c > 1"
    ).fetchall()
    if not groups:
        return
    for doctor_id, appointment_type_id, _count in groups:
        rows = conn.exec_driver_sql(
            "SELECT id FROM calendars WHERE doctor_id = ? AND appointment_type_id = ? ORDER BY id",
            (doctor_id, appointment_type_id),
        ).fetchall()
        keep_id = rows[0][0]
        duplicate_ids = [row[0] for row in rows[1:]]
        for dup_id in duplicate_ids:
            conn.exec_driver_sql("UPDATE working_hours SET calendar_id = ? WHERE calendar_id = ?", (keep_id, dup_id))
            conn.exec_driver_sql("UPDATE blocked_periods SET calendar_id = ? WHERE calendar_id = ?", (keep_id, dup_id))
            conn.exec_driver_sql("UPDATE appointments SET calendar_id = ? WHERE calendar_id = ?", (keep_id, dup_id))
            conn.exec_driver_sql("DELETE FROM calendars WHERE id = ?", (dup_id,))
    conn.commit()
    # working_hours has its own UniqueConstraint(calendar_id, weekday, start_time); merging
    # calendars can reveal identical windows that were duplicated across the merged calendars.
    conn.exec_driver_sql("""
        DELETE FROM working_hours WHERE id NOT IN (
            SELECT MIN(id) FROM working_hours GROUP BY calendar_id, weekday, start_time
        )
    """)
    conn.commit()

