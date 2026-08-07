"""Single service layer for patient CRUD.

Both the public REST router (app/routers/patients.py) and the Vapi webhook
(app/routers/vapi.py) call these same functions — that's the "voice agent
uses the same service layer as the API" requirement from the PDF, and it's
what keeps validation/business rules from drifting between the two entry
points.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models import Patient
from app.schemas import PatientCreate, PatientUpdate


def create_patient(db: Session, data: PatientCreate) -> Patient:
    patient = Patient(**data.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def get_patient(db: Session, patient_id: str) -> Optional[Patient]:
    return (
        db.query(Patient)
        .filter(Patient.patient_id == patient_id, Patient.deleted_at.is_(None))
        .first()
    )


def find_by_phone(db: Session, phone_number: str) -> Optional[Patient]:
    """Powers duplicate-call detection: 'we already have a record for X, update instead?'"""
    digits = "".join(ch for ch in phone_number if ch.isdigit())
    return (
        db.query(Patient)
        .filter(Patient.phone_number == digits, Patient.deleted_at.is_(None))
        .first()
    )


def list_patients(
    db: Session,
    last_name: Optional[str] = None,
    date_of_birth: Optional[str] = None,
    phone_number: Optional[str] = None,
) -> list[Patient]:
    filters = [Patient.deleted_at.is_(None)]
    if last_name:
        filters.append(Patient.last_name.ilike(last_name))
    if date_of_birth:
        filters.append(Patient.date_of_birth == date_of_birth)
    if phone_number:
        digits = "".join(ch for ch in phone_number if ch.isdigit())
        filters.append(Patient.phone_number == digits)
    return db.query(Patient).filter(and_(*filters)).order_by(Patient.created_at.desc()).all()


def update_patient(db: Session, patient: Patient, data: PatientUpdate) -> Patient:
    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(patient, field, value)
    patient.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(patient)
    return patient


def soft_delete_patient(db: Session, patient: Patient) -> Patient:
    patient.deleted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(patient)
    return patient
