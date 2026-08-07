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
MAX_AGE_YEARS = 120  # no living patient is older; catches "1700"/"1888" mishears
STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC",
}
US_STATES = set(STATE_NAMES.values())
# Deepgram hands back homophones for spoken sex; map them before the enum rejects them.
SEX_ALIASES = {
    "mail": "Male", "male": "Male", "mayle": "Male", "m": "Male",
    "femail": "Female", "female": "Female", "f": "Female",
    "other": "Other", "non-binary": "Other", "nonbinary": "Other",
    "decline to answer": "Decline to Answer", "decline": "Decline to Answer",
    "prefer not to say": "Decline to Answer", "prefer not to answer": "Decline to Answer",
}


class Sex(str, Enum):
    male = "Male"
    female = "Female"
    other = "Other"
    decline = "Decline to Answer"


WORD_DIGITS = {
    "zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
    "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
REPEATS = {"double": 2, "triple": 3}


def _spoken_to_digits(v: str) -> str:
    """Callers say numbers out loud: "double 1", "five five five", "oh".

    The LLM usually normalizes these, but it guesses wrong often enough
    (it read "double 1" as a single 1) that the same parse runs server-side.
    """
    tokens = re.findall(r"[a-z]+|\d", (v or "").lower())
    out: list[str] = []
    repeat = 1
    for token in tokens:
        if token in REPEATS:
            repeat = REPEATS[token]
            continue
        digit = token if token.isdigit() else WORD_DIGITS.get(token)
        if digit is None:
            repeat = 1  # stray word ("my", "number") — drop it and any pending repeat
            continue
        out.append(digit * repeat)
        repeat = 1
    return "".join(out)


def _digits_only(v: str, field_name: str) -> str:
    digits = _spoken_to_digits(v)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]  # leading country code
    if len(digits) != 10:
        raise ValueError(f"{field_name} must be a valid U.S. 10-digit phone number")
    return digits


def _normalize_sex(v):
    if not isinstance(v, str):
        return v
    return SEX_ALIASES.get(v.strip().lower(), v)


def _normalize_state(v: Optional[str]) -> Optional[str]:
    if v is None:
        return v
    cleaned = v.strip().lower()
    if cleaned in STATE_NAMES:
        return STATE_NAMES[cleaned]
    upper = v.strip().upper()
    if upper not in US_STATES:
        raise ValueError("state must be a valid U.S. state (name or 2-letter abbreviation)")
    return upper


def _check_dob(v: Optional[date]) -> Optional[date]:
    if v is None:
        return v
    if v > date.today():
        raise ValueError("date_of_birth cannot be in the future")
    if v.year < date.today().year - MAX_AGE_YEARS:
        raise ValueError(f"date_of_birth implies an age over {MAX_AGE_YEARS}; please confirm the year")
    return v


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
    state: str
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

    @field_validator("sex", mode="before")
    @classmethod
    def _validate_sex(cls, v):
        return _normalize_sex(v)

    @field_validator("date_of_birth")
    @classmethod
    def _validate_dob(cls, v: date) -> date:
        return _check_dob(v)

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
        return _normalize_state(v)

    @field_validator("zip_code")
    @classmethod
    def _validate_zip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = re.sub(r"[^\d-]", "", v)  # "7 5 5 2 3" spoken back as separate digits
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
    state: Optional[str] = None
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

    @field_validator("sex", mode="before")
    @classmethod
    def _validate_sex(cls, v):
        return _normalize_sex(v)

    @field_validator("date_of_birth")
    @classmethod
    def _validate_dob(cls, v: Optional[date]) -> Optional[date]:
        return _check_dob(v)

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
        return _normalize_state(v)

    @field_validator("zip_code")
    @classmethod
    def _validate_zip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = re.sub(r"[^\d-]", "", v)
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
