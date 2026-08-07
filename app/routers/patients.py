"""Public REST API — GET/POST/PUT/DELETE /patients, per the PDF spec.

Every response uses the {"data": ..., "error": null} envelope. Validation
errors from Pydantic surface as 422 with the field-level message; not-found
as 404; anything unexpected as 500 — all still wrapped in the envelope by
the exception handlers registered in app/main.py.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import services
from app.db import get_db
from app.schemas import Envelope, PatientCreate, PatientOut, PatientUpdate

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("", response_model=Envelope)
def list_patients(
    last_name: Optional[str] = None,
    date_of_birth: Optional[str] = None,
    phone_number: Optional[str] = None,
    db: Session = Depends(get_db),
):
    patients = services.list_patients(db, last_name, date_of_birth, phone_number)
    return Envelope(data=[PatientOut.model_validate(p).model_dump(mode="json") for p in patients])


@router.get("/{patient_id}", response_model=Envelope)
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = services.get_patient(db, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="patient not found")
    return Envelope(data=PatientOut.model_validate(patient).model_dump(mode="json"))


@router.post("", response_model=Envelope, status_code=201)
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    patient = services.create_patient(db, payload)
    return Envelope(data=PatientOut.model_validate(patient).model_dump(mode="json"))


@router.put("/{patient_id}", response_model=Envelope)
def update_patient(patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)):
    patient = services.get_patient(db, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="patient not found")
    patient = services.update_patient(db, patient, payload)
    return Envelope(data=PatientOut.model_validate(patient).model_dump(mode="json"))


@router.delete("/{patient_id}", response_model=Envelope)
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = services.get_patient(db, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="patient not found")
    services.soft_delete_patient(db, patient)
    return Envelope(data={"patient_id": patient_id, "deleted": True})
