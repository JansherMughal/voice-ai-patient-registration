"""ORM models — the schema is the single source of truth for constraints (PDF requirement)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Patient(Base):
    __tablename__ = "patients"

    # Stored as String(36) rather than a native UUID column so the same model
    # runs unchanged on SQLite (local/tests) and Postgres (Railway).
    patient_id = Column(String(36), primary_key=True, default=_uuid)

    first_name = Column(String(50), nullable=False)
    last_name = Column(String(50), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    sex = Column(String(20), nullable=False)  # enum enforced in Pydantic schema

    phone_number = Column(String(10), nullable=False, index=True)
    email = Column(String(255), nullable=True)

    address_line_1 = Column(String(255), nullable=False)
    address_line_2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=False)
    state = Column(String(2), nullable=False)
    zip_code = Column(String(10), nullable=False)

    insurance_provider = Column(String(150), nullable=True)
    insurance_member_id = Column(String(50), nullable=True)
    preferred_language = Column(String(50), nullable=False, default="English")
    emergency_contact_name = Column(String(150), nullable=True)
    emergency_contact_phone = Column(String(10), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)  # soft delete

    transcripts = relationship("CallTranscript", back_populates="patient")


class CallTranscript(Base):
    """Bonus: one row per completed Vapi call, linked to the patient it registered/updated."""
    __tablename__ = "call_transcripts"

    id = Column(String(36), primary_key=True, default=_uuid)
    patient_id = Column(String(36), ForeignKey("patients.patient_id"), nullable=True)
    vapi_call_id = Column(String(100), nullable=True, index=True)
    transcript = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    patient = relationship("Patient", back_populates="transcripts")
