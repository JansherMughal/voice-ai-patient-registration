"""FastAPI entrypoint: wires routers, exception handlers, table creation, and seed data."""
import logging
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.db import Base, engine, SessionLocal
from app.models import Patient
from app.routers import patients, vapi, dashboard

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Voice AI Patient Registration API")

app.include_router(patients.router)
app.include_router(vapi.router)
app.include_router(dashboard.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """Keeps 404s (and any other raised HTTPException) inside the {"data","error"} envelope."""
    return JSONResponse(status_code=exc.status_code, content={"data": None, "error": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    """Pydantic validation failures -> 422, envelope, one readable message per bad field."""
    messages = [f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors()]
    return JSONResponse(status_code=422, content={"data": None, "error": "; ".join(messages)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    logging.getLogger("api").exception("unhandled error")
    return JSONResponse(status_code=500, content={"data": None, "error": "internal server error"})


def _seed_if_empty():
    db = SessionLocal()
    try:
        if db.query(Patient).count() > 0:
            return
        db.add_all([
            Patient(
                first_name="Jane", last_name="Doe", date_of_birth=date(1990, 5, 14),
                sex="Female", phone_number="5551234567", email="jane.doe@example.com",
                address_line_1="123 Main St", city="Austin", state="TX", zip_code="73301",
                preferred_language="English",
            ),
            Patient(
                first_name="Carlos", last_name="Ramirez", date_of_birth=date(1985, 11, 2),
                sex="Male", phone_number="5559876543", email="carlos.r@example.com",
                address_line_1="456 Oak Ave", city="Miami", state="FL", zip_code="33101",
                preferred_language="Spanish",
            ),
        ])
        db.commit()
    finally:
        db.close()


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    _seed_if_empty()


@app.get("/health")
def health():
    return {"data": {"status": "ok"}, "error": None}
