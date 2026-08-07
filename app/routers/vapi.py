"""Vapi integration: one webhook endpoint handling two message types.

1. "tool-calls"        — the assistant invoking lookup_patient / register_patient /
                          update_patient mid-conversation. We must reply with
                          {"results": [{"toolCallId": ..., "result": <string>}]}
                          so Vapi can read the result back to the caller.
2. "end-of-call-report" — fired once the call ends; carries the transcript/summary,
                          which we store linked to whatever patient that call touched.

Vapi signs every request with the secret configured on the assistant's server
config, sent as the `x-vapi-secret` header — we reject anything that doesn't
match so this endpoint can't be used to write arbitrary patient data from the
open internet.
"""
import logging

from fastapi import APIRouter, Header, HTTPException, Request, Depends
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import services
from app.config import VAPI_WEBHOOK_SECRET
from app.db import get_db
from app.models import CallTranscript
from app.schemas import PatientCreate, PatientUpdate

logger = logging.getLogger("vapi_webhook")
router = APIRouter(prefix="/vapi", tags=["vapi"])

# call_id -> patient_id, so the end-of-call-report (which only has the call id)
# can be linked to the patient that register_patient/update_patient just touched.
# In-memory is fine for a single-instance take-home deploy; see README limitations.
_call_patient_map: dict[str, str] = {}


def _verify_secret(x_vapi_secret: str | None):
    if not VAPI_WEBHOOK_SECRET or x_vapi_secret != VAPI_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def _format_validation_error(exc: ValidationError) -> str:
    """Turn a Pydantic error into one sentence the LLM can speak back to re-prompt a field."""
    first = exc.errors()[0]
    field = first["loc"][-1]
    return f"{field}: {first['msg']}"


def _handle_lookup_patient(args: dict, db: Session) -> str:
    phone = args.get("phone_number", "")
    patient = services.find_by_phone(db, phone)
    if not patient:
        return "no_existing_patient"
    return f"existing_patient_found|{patient.patient_id}|{patient.first_name}|{patient.last_name}"


def _handle_register_patient(args: dict, db: Session, call_id: str | None) -> str:
    try:
        payload = PatientCreate(**args)
    except ValidationError as exc:
        return f"validation_error|{_format_validation_error(exc)}"
    patient = services.create_patient(db, payload)
    if call_id:
        _call_patient_map[call_id] = patient.patient_id
    logger.info("registered patient %s %s (%s)", patient.first_name, patient.last_name, patient.patient_id)
    return f"success|{patient.patient_id}|{patient.first_name}"


def _handle_update_patient(args: dict, db: Session, call_id: str | None) -> str:
    patient_id = args.pop("patient_id", None)
    patient = services.get_patient(db, patient_id) if patient_id else None
    if not patient:
        return "error|patient not found for update"
    try:
        payload = PatientUpdate(**args)
    except ValidationError as exc:
        return f"validation_error|{_format_validation_error(exc)}"
    patient = services.update_patient(db, patient, payload)
    if call_id:
        _call_patient_map[call_id] = patient.patient_id
    logger.info("updated patient %s %s (%s)", patient.first_name, patient.last_name, patient.patient_id)
    return f"success|{patient.patient_id}|{patient.first_name}"


_HANDLERS = {
    "lookup_patient": _handle_lookup_patient,
    "register_patient": _handle_register_patient,
    "update_patient": _handle_update_patient,
}


@router.post("/webhook")
async def vapi_webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_vapi_secret: str | None = Header(default=None, alias="x-vapi-secret"),
):
    _verify_secret(x_vapi_secret)
    body = await request.json()
    message = body.get("message", {})
    msg_type = message.get("type")

    if msg_type == "tool-calls":
        call_id = (message.get("call") or {}).get("id")
        results = []
        for tool_call in message.get("toolCallList", []):
            name = tool_call["function"]["name"]
            args = tool_call["function"].get("arguments", {}) or {}
            handler = _HANDLERS.get(name)
            if handler is None:
                result = f"error|unknown tool {name}"
            else:
                try:
                    if name == "lookup_patient":
                        result = handler(args, db)
                    else:
                        result = handler(args, db, call_id)
                except Exception as exc:  # DB write failed etc — never leave the caller in silence
                    logger.exception("tool call %s failed", name)
                    result = f"error|{exc}"
            results.append({"toolCallId": tool_call["id"], "result": result})
        return {"results": results}

    if msg_type == "end-of-call-report":
        call = message.get("call") or {}
        call_id = call.get("id")
        transcript = message.get("transcript")
        summary = message.get("summary")
        row = CallTranscript(
            patient_id=_call_patient_map.pop(call_id, None) if call_id else None,
            vapi_call_id=call_id,
            transcript=transcript,
            summary=summary,
        )
        db.add(row)
        db.commit()
        logger.info("stored transcript for call %s", call_id)
        return {"received": True}

    # Any other Vapi message type (status-update, speech-update, etc.) — ignore.
    return {"received": True}
