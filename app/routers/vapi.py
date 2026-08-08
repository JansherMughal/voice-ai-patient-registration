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
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request, Depends
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import services
from app.config import VAPI_WEBHOOK_SECRET
from app.db import get_db
from app.models import CallTranscript
from app.schemas import PatientCreate, PatientUpdate, _spoken_to_digits

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
    """Turn a Pydantic error into one sentence the LLM can speak back to re-prompt a field.

    Model-level validators (the ZIP/state cross-check) report an empty `loc`,
    so there is no single field to name — their message already says which
    fields are involved. Indexing into that empty tuple crashed a live call.
    """
    first = exc.errors()[0]
    msg = first["msg"]
    # Pydantic prefixes ValueError messages; the agent reads this aloud.
    if msg.startswith("Value error, "):
        msg = msg[len("Value error, "):]
    loc = first["loc"]
    return f"{loc[-1]}: {msg}" if loc else msg


def _handle_lookup_patient(args: dict, db: Session) -> str:
    """Also acts as the digit counter for the agent.

    Transcripts showed the LLM consistently miscounting digits out loud — it
    called the same 10-digit number "8 digits", then "9", then "11". Counting is
    exact here and guesswork there, so the agent is told to send whatever it has
    and let this answer decide.
    """
    digits = _spoken_to_digits(args.get("phone_number", ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]  # leading country code

    if len(digits) != 10:
        short_by = 10 - len(digits)
        if short_by > 0:
            return f"invalid_phone|so far I have {len(digits)} of 10 digits, ask for {short_by} more"
        return f"invalid_phone|that's {len(digits)} digits, 10 too many by {-short_by}; ask them to say the whole number again"

    patient = services.find_by_phone(db, digits)
    if not patient:
        return f"no_existing_patient|{digits}"
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
            args = tool_call["function"].get("arguments") or {}
            # Vapi sends arguments as a JSON string for some model providers, dict for others.
            if isinstance(args, str):
                args = json.loads(args or "{}")
            logger.info("tool call %s args=%s", name, args)
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
