"""Local SQLite staging store shaped like a future Dynamics 365 lead schema.

This module never connects to Dataverse or Dynamics 365. It only creates and
reads ``rmc_leads_staging.db`` beside this file.
"""

from datetime import datetime
from pathlib import Path

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DB_FILENAME = "rmc_leads_staging.db"
DB_PATH = Path(__file__).resolve().parent / DB_FILENAME
DATABASE_URL = "sqlite:///" + DB_PATH.as_posix()

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
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
