"""Vapi sends tool-call arguments as a JSON string for some model providers."""
import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app

SECRET = "test-secret"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(config, "VAPI_WEBHOOK_SECRET", SECRET)
    from app.routers import vapi as vapi_router
    monkeypatch.setattr(vapi_router, "VAPI_WEBHOOK_SECRET", SECRET)


def _tool_call(name, arguments):
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": "call-1"},
            "toolCallList": [
                {"id": "tc-1", "function": {"name": name, "arguments": arguments}}
            ],
        }
    }


PATIENT = {
    "first_name": "John",
    "last_name": "Butler",
    "date_of_birth": "1988-06-25",
    "sex": "Male",
    "phone_number": "1234567998",
    "address_line_1": "2 Bovat",
    "city": "New York City",
    "state": "NY",
    "zip_code": "75523",
}


@pytest.mark.parametrize("as_string", [False, True])
def test_register_patient_accepts_dict_or_json_string_args(as_string):
    args = json.dumps(PATIENT) if as_string else PATIENT
    with TestClient(app) as client:
        resp = client.post(
            "/vapi/webhook",
            json=_tool_call("register_patient", args),
            headers={"x-vapi-secret": SECRET},
        )
    assert resp.status_code == 200
    result = resp.json()["results"][0]["result"]
    assert result.startswith("success|"), result


def test_implausible_dob_comes_back_as_a_speakable_validation_error():
    """Live call mishear: "1988" transcribed as "1888" and saved silently."""
    with TestClient(app) as client:
        resp = client.post(
            "/vapi/webhook",
            json=_tool_call("register_patient", {**PATIENT, "date_of_birth": "1888-06-25"}),
            headers={"x-vapi-secret": SECRET},
        )
    result = resp.json()["results"][0]["result"]
    assert result.startswith("validation_error|date_of_birth"), result


def test_lookup_patient_accepts_json_string_args():
    with TestClient(app) as client:
        resp = client.post(
            "/vapi/webhook",
            json=_tool_call("lookup_patient", json.dumps({"phone_number": "5559999999"})),
            headers={"x-vapi-secret": SECRET},
        )
    assert resp.json()["results"][0]["result"] == "no_existing_patient"


def test_end_of_call_report_stores_a_transcript_linked_to_the_patient():
    """The report carries only the Vapi call id, so the link comes from the
    call_id -> patient_id map populated during the register_patient tool call."""
    headers = {"x-vapi-secret": SECRET}
    with TestClient(app) as client:
        reg = client.post("/vapi/webhook", json=_tool_call("register_patient", PATIENT), headers=headers)
        patient_id = reg.json()["results"][0]["result"].split("|")[1]

        client.post(
            "/vapi/webhook",
            json={
                "message": {
                    "type": "end-of-call-report",
                    "call": {"id": "call-1"},
                    "transcript": "AI: Hi, I'm Ava.\nUser: Hello.",
                    "summary": "Registered John Butler.",
                }
            },
            headers=headers,
        )

        rows = client.get("/transcripts").json()["data"]

    assert len(rows) == 1
    assert rows[0]["patient_id"] == patient_id
    assert rows[0]["patient_name"] == "John Butler"
    assert "I'm Ava" in rows[0]["transcript"]
