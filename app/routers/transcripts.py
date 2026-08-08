"""Read access to stored call transcripts.

The webhook writes one row per completed call (app/routers/vapi.py, the
end-of-call-report branch); this is how you read them back to review how the
agent actually behaved on a given call.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import services
from app.db import get_db
from app.schemas import Envelope

router = APIRouter(prefix="/transcripts", tags=["transcripts"])


@router.get("", response_model=Envelope)
def list_transcripts(
    patient_id: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    rows = services.list_transcripts(db, patient_id, limit)
    return Envelope(
        data=[
            {
                "id": t.id,
                "patient_id": t.patient_id,
                "patient_name": f"{t.patient.first_name} {t.patient.last_name}" if t.patient else None,
                "vapi_call_id": t.vapi_call_id,
                "summary": t.summary,
                "transcript": t.transcript,
                "created_at": t.created_at.isoformat(),
            }
            for t in rows
        ]
    )
