"""Local SQLite staging store shaped like a future Dynamics 365 lead schema.

This module never connects to Dataverse or Dynamics 365. It only creates and
reads ``rmc_leads_staging.db``. On Streamlit Cloud the repository copy is not
writable, so the file is copied to a temporary directory before any refresh.
"""

import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

DB_FILENAME = "rmc_leads_staging.db"
BUNDLED_DB_PATH = Path(__file__).resolve().parent / DB_FILENAME


def _sqlite_file_is_writable(path: Path) -> bool:
    """True when SQLite can create a table in this file."""
    if not path.exists():
        return False
    try:
        connection = sqlite3.connect(path, timeout=5)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS _write_probe (id INTEGER)")
            connection.execute("DROP TABLE _write_probe")
            connection.commit()
        finally:
            connection.close()
        return True
    except sqlite3.Error:
        return False


def _runtime_db_path() -> Path:
    if _sqlite_file_is_writable(BUNDLED_DB_PATH):
        return BUNDLED_DB_PATH
    runtime_dir = Path(tempfile.gettempdir()) / "mr-project"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_dir / DB_FILENAME
    if BUNDLED_DB_PATH.exists():
        bundled_is_newer = (
            not runtime_path.exists()
            or BUNDLED_DB_PATH.stat().st_mtime > runtime_path.stat().st_mtime
        )
        if bundled_is_newer:
            shutil.copy2(BUNDLED_DB_PATH, runtime_path)
    return runtime_path


DB_PATH = _runtime_db_path()
DATABASE_URL = "sqlite:///" + DB_PATH.as_posix()

engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    connect_args={"check_same_thread": False, "timeout": 30},
)


@event.listens_for(engine, "connect")
def _set_sqlite_timeout(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout = 30000")
    cursor.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Lead(Base):
    """Staging lead. Column names mirror the future CRM schema."""

    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    Project_Name = Column(String(162), nullable=False)
    External_Project_Code = Column(String(100), unique=True, nullable=False)
    Lead_Source_Name = Column(String(50), nullable=False)
    Country = Column(String(50), default="Sweden", nullable=False)
    Construction_Project_Phase = Column(Integer, nullable=False)
    Total_Value = Column(Float, nullable=True)
    Currency = Column(String(10), default="SEK", nullable=False)
    Notes = Column(Text, nullable=True)
    Tender_Closing_Date = Column(DateTime, nullable=True)
    Tender_Award_Date = Column(DateTime, nullable=True)
    Construction_Start_Date = Column(DateTime, nullable=True)
    Construction_End_Date = Column(DateTime, nullable=True)
    Address_1_Postal_Code = Column(String(20), nullable=True)
    Address_1_City = Column(String(40), nullable=True)
    Address_1_State_Province = Column(String(100), nullable=True)
    Address_1_Country = Column(String(50), default="Sweden", nullable=False)
    Address_1_Latitude = Column(Float, nullable=True)
    Address_1_Longitude = Column(Float, nullable=True)
    Created_On = Column(DateTime, default=datetime.utcnow, nullable=False)


def init_db() -> None:
    """Create the staging tables if they do not exist yet."""
    Base.metadata.create_all(engine)


class EtlRun(Base):
    """One completed refresh. Used to keep the daily Swedish morning pull to a single run."""

    __tablename__ = "etl_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    Finished_On = Column(DateTime, default=datetime.utcnow, nullable=False)
    Status = Column(String(20), nullable=False)
    Summary = Column(Text, nullable=True)


def last_successful_run(session=None):
    """Naive UTC timestamp of the latest successful refresh, or None."""
    init_db()
    own_session = session is None
    session = session or SessionLocal()
    try:
        run = (
            session.query(EtlRun)
            .filter(EtlRun.Status == "ok")
            .order_by(EtlRun.Finished_On.desc())
            .first()
        )
        return None if run is None else run.Finished_On
    finally:
        if own_session:
            session.close()


def newest_lead_created_on():
    """Fallback clock for a snapshot that predates the run log."""
    init_db()
    session = SessionLocal()
    try:
        return session.query(Lead.Created_On).order_by(Lead.Created_On.desc()).limit(1).scalar()
    finally:
        session.close()


def lead_count() -> int:
    init_db()
    session = SessionLocal()
    try:
        return session.query(Lead).count()
    finally:
        session.close()


def record_etl_run(summary: dict) -> None:
    """Remember a refresh that stored leads. A total failure stays unlogged so the next open can retry."""
    if summary.get("total_upserted", 0) <= 0 and summary.get("errors"):
        return
    init_db()
    brief = ", ".join(f"{name} {count}" for name, count in summary.get("upserted", {}).items())
    session = SessionLocal()
    try:
        session.add(
            EtlRun(
                Finished_On=datetime.utcnow(),
                Status="ok",
                Summary=brief[:500],
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def upsert_leads(rows: list[dict]) -> int:
    """Insert new leads and update existing ones matched on external code.

    ``Created_On`` is left untouched when a lead is refreshed.
    """
    init_db()
    session = SessionLocal()
    written = 0
    try:
        for row in rows:
            code = row.get("External_Project_Code")
            name = row.get("Project_Name")
            if not code or not name:
                continue
            existing = (
                session.query(Lead)
                .filter(Lead.External_Project_Code == code)
                .one_or_none()
            )
            if existing is None:
                session.add(Lead(**row))
            else:
                for key, value in row.items():
                    if key in {"Created_On", "id"}:
                        continue
                    setattr(existing, key, value)
            written += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return written
