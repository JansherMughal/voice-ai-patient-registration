"""Pydantic schemas — this is where server-side validation actually lives.

The PDF is explicit: "Validate all inputs server-side (do not rely solely on
the voice agent for validation)." Vapi's LLM can still mis-hear or mis-format
a field; every request (from the voice webhook or a plain curl) runs through
the same validators here before ever reaching the DB.
"""
import re
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict

NAME_RE = re.compile(r"^[A-Za-z' -]{1,50}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}


class Sex(str, Enum):
    male = "Male"
    female = "Female"
    other = "Other"
    decline = "Decline to Answer"


def _digits_only(v: str, field_name: str) -> str:
    digits = re.sub(r"\D", "", v or "")
    if len(digits) != 10:
        raise ValueError(f"{field_name} must be a valid U.S. 10-digit phone number")
    return digits


class PatientBase(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    date_of_birth: date
    sex: Sex
    phone_number: str
    email: Optional[EmailStr] = None
    address_line_1: str = Field(..., min_length=1, max_length=255)
    address_line_2: Optional[str] = Field(None, max_length=255)
    city: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=2, max_length=2)
    zip_code: str
    insurance_provider: Optional[str] = Field(None, max_length=150)
    insurance_member_id: Optional[str] = Field(None, max_length=50)
    preferred_language: str = Field("English", max_length=50)
    emergency_contact_name: Optional[str] = Field(None, max_length=150)
    emergency_contact_phone: Optional[str] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("must be 1-50 alphabetic characters, hyphens, or apostrophes")
        return v

    @field_validator("date_of_birth")
    @classmethod
    def _validate_dob(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return v

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        return _digits_only(v, "phone_number")

    @field_validator("emergency_contact_phone")
    @classmethod
    def _validate_emergency_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        return _digits_only(v, "emergency_contact_phone")

    @field_validator("state")
    @classmethod
    def _validate_state(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.upper()
        if v not in US_STATES:
            raise ValueError("state must be a valid 2-letter U.S. state abbreviation")
        return v

    @field_validator("zip_code")
    @classmethod
    def _validate_zip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not ZIP_RE.match(v):
            raise ValueError("zip_code must be 5-digit or ZIP+4 U.S. format")
        return v


class PatientCreate(PatientBase):
    """Everything the voice agent / POST /patients must supply."""
    pass


class PatientUpdate(BaseModel):
    """PUT /patients/:id — every field optional, only what's provided gets changed."""
    first_name: Optional[str] = Field(None, min_length=1, max_length=50)
    last_name: Optional[str] = Field(None, min_length=1, max_length=50)
    date_of_birth: Optional[date] = None
    sex: Optional[Sex] = None
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None
    address_line_1: Optional[str] = Field(None, min_length=1, max_length=255)
    address_line_2: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, min_length=1, max_length=100)
    state: Optional[str] = Field(None, min_length=2, max_length=2)
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = Field(None, max_length=150)
    insurance_member_id: Optional[str] = Field(None, max_length=50)
    preferred_language: Optional[str] = Field(None, max_length=50)
    emergency_contact_name: Optional[str] = Field(None, max_length=150)
    emergency_contact_phone: Optional[str] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def _validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not NAME_RE.match(v):
            raise ValueError("must be 1-50 alphabetic characters, hyphens, or apostrophes")
        return v

    @field_validator("date_of_birth")
    @classmethod
    def _validate_dob(cls, v: Optional[date]) -> Optional[date]:
        if v is not None and v > date.today():
            raise ValueError("date_of_birth cannot be in the future")
        return v

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: Optional[str]) -> Optional[str]:
        return _digits_only(v, "phone_number") if v is not None else v

    @field_validator("emergency_contact_phone")
    @classmethod
    def _validate_emergency_phone(cls, v: Optional[str]) -> Optional[str]:
        return _digits_only(v, "emergency_contact_phone") if v else None

    @field_validator("state")
    @classmethod
    def _validate_state(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.upper()
        if v not in US_STATES:
            raise ValueError("state must be a valid 2-letter U.S. state abbreviation")
        return v

    @field_validator("zip_code")
    @classmethod
    def _validate_zip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not ZIP_RE.match(v):
            raise ValueError("zip_code must be 5-digit or ZIP+4 U.S. format")
        return v


class PatientOut(PatientBase):
    model_config = ConfigDict(from_attributes=True)

    patient_id: str
    created_at: datetime
    updated_at: datetime


class Envelope(BaseModel):
    """Every API response — success or error — shares this shape, per PDF spec."""
    data: Optional[object] = None
    error: Optional[str] = None
