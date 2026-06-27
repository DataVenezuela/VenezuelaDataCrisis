from __future__ import annotations

from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, DateTime, JSON, event
from sqlalchemy.orm import declarative_base, sessionmaker
from shared.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

# WAL (Write-Ahead Logging) previene errores de bloqueo (database is locked) en SQLite bajo lecturas/escrituras concurrentes.
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class DocumentModel(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(String(100), nullable=False)
    source_name = Column(String(200), nullable=False)
    source_url = Column(String(500), nullable=False)
    title = Column(String(300), nullable=True)
    text = Column(Text, nullable=False)
    raw_hash = Column(String(64), unique=True, index=True, nullable=False)
    trust_tier = Column(String(10), nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    metadata_json = Column(JSON, nullable=True)


class ClaimModel(Base):
    __tablename__ = "claims"

    claim_id = Column(String(100), primary_key=True, index=True)
    fingerprint = Column(String(64), index=True, nullable=False)
    event_id = Column(String(100), nullable=False)
    source_id = Column(String(100), nullable=False)
    source_name = Column(String(200), nullable=False)
    source_url = Column(String(500), nullable=False)
    claim_type = Column(String(100), index=True, nullable=False)
    description = Column(Text, nullable=False)
    location_text = Column(String(300), nullable=True)
    confidence_score = Column(Float, default=0.0)
    verification_status = Column(String(50), default="new", index=True)
    evidence_text = Column(Text, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    metadata_json = Column(JSON, nullable=True)


def init_db() -> None:
    """Crea todas las tablas definidas en los modelos en caso de que no existan."""
    Base.metadata.create_all(bind=engine)
