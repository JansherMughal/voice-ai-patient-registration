"""API-layer tests against an isolated in-memory SQLite DB (bonus: automated tests)."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

VALID_PATIENT = {
    "first_name": "Alice",
    "last_name": "Nguyen",
    "date_of_birth": "1992-03-10",
    "sex": "Female",
    "phone_number": "512-555-0199",
    "address_line_1": "789 Elm St",
    "city": "Dallas",
    "state": "tx",
    "zip_code": "75201",
}


def test_create_patient_success():
    resp = client.post("/patients", json=VALID_PATIENT)
    assert resp.status_code == 201
    body = resp.json()
    assert body["error"] is None
    assert body["data"]["first_name"] == "Alice"
    assert body["data"]["phone_number"] == "5125550199"  # normalized to digits
    assert body["data"]["state"] == "TX"  # normalized to uppercase
    assert "patient_id" in body["data"]


def test_create_patient_future_dob_rejected():
    bad = {**VALID_PATIENT, "date_of_birth": "2999-01-01"}
    resp = client.post("/patients", json=bad)
    assert resp.status_code == 422
    assert resp.json()["data"] is None
    assert "date_of_birth" in resp.json()["error"]


def test_create_patient_invalid_phone_rejected():
    bad = {**VALID_PATIENT, "phone_number": "123"}
    resp = client.post("/patients", json=bad)
    assert resp.status_code == 422


def test_get_patient_not_found():
    resp = client.get("/patients/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"] == "patient not found"


def test_list_filter_by_last_name():
    client.post("/patients", json=VALID_PATIENT)
    resp = client.get("/patients", params={"last_name": "Nguyen"})
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1


def test_update_partial():
    created = client.post("/patients", json=VALID_PATIENT).json()["data"]
    resp = client.put(f"/patients/{created['patient_id']}", json={"city": "Houston"})
    assert resp.status_code == 200
    assert resp.json()["data"]["city"] == "Houston"
    assert resp.json()["data"]["last_name"] == "Nguyen"  # untouched


def test_soft_delete_hides_from_list_and_get():
    created = client.post("/patients", json=VALID_PATIENT).json()["data"]
    pid = created["patient_id"]
    resp = client.delete(f"/patients/{pid}")
    assert resp.status_code == 200
    assert client.get(f"/patients/{pid}").status_code == 404
    assert all(p["patient_id"] != pid for p in client.get("/patients").json()["data"])


def test_persists_across_requests_same_process():
    client.post("/patients", json=VALID_PATIENT)
    resp = client.get("/patients", params={"phone_number": "5125550199"})
    assert len(resp.json()["data"]) == 1
