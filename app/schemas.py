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

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator, ConfigDict

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

# ZIP prefix (first 3 digits) -> state, as inclusive ranges. Used only to catch
# a ZIP that provably belongs to a different state than the caller gave —
# transcripts produced "Chicago, Illinois, 75050" and "New York, 79234", both
# of which are Texas ZIPs. Deliberately incomplete: a prefix not listed here
# passes, so gaps in this table can never reject a legitimate address.
ZIP_PREFIX_RANGES: list[tuple[int, int, str]] = [
    (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"), (39, 49, "ME"),
    (50, 54, "VT"), (56, 59, "VT"), (60, 69, "CT"), (70, 89, "NJ"),
    (100, 149, "NY"), (150, 196, "PA"), (197, 199, "DE"),
    (200, 200, "DC"), (202, 205, "DC"), (206, 219, "MD"),
    (220, 246, "VA"), (247, 268, "WV"), (270, 289, "NC"), (290, 299, "SC"),
    (300, 319, "GA"), (320, 339, "FL"), (341, 342, "FL"), (344, 344, "FL"),
    (346, 347, "FL"), (349, 349, "FL"),
    (350, 352, "AL"), (354, 369, "AL"), (370, 385, "TN"), (386, 397, "MS"),
    (398, 399, "GA"), (400, 427, "KY"), (430, 459, "OH"), (460, 479, "IN"),
    (480, 499, "MI"), (500, 528, "IA"), (530, 549, "WI"), (550, 567, "MN"),
    (570, 577, "SD"), (580, 588, "ND"), (590, 599, "MT"),
    (600, 620, "IL"), (622, 629, "IL"), (630, 658, "MO"), (660, 679, "KS"),
    (680, 693, "NE"), (700, 701, "LA"), (703, 708, "LA"), (710, 714, "LA"),
    (716, 729, "AR"), (730, 731, "OK"), (734, 749, "OK"),
    (750, 799, "TX"), (800, 816, "CO"), (820, 831, "WY"), (832, 838, "ID"),
    (840, 847, "UT"), (850, 860, "AZ"), (863, 865, "AZ"),
    (870, 884, "NM"), (889, 898, "NV"), (900, 908, "CA"), (910, 928, "CA"),
    (930, 961, "CA"), (967, 968, "HI"), (970, 979, "OR"), (980, 994, "WA"),
    (995, 999, "AK"),
]
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


def _digits_only(v: str, field_name: str, strict: bool = False) -> str:
    """Normalize to 10 digits. `strict` additionally enforces NANP rules.

    Strict runs on writes only. Reads stay lenient so rows saved before these
    rules existed remain retrievable — tightening validation should not make
    old data unreadable.
    """
    digits = _spoken_to_digits(v)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]  # leading country code
    if len(digits) != 10:
        raise ValueError(f"{field_name} must be a valid U.S. 10-digit phone number")
    if strict and digits[0] in "01":
        # NANP area codes never begin with 0 or 1. Callers dictated
        # "0345329998" and "0986567893" — ten digits each, neither U.S.
        # The sibling NANP rule (exchange can't start with 0/1) is deliberately
        # not enforced: it would reject the familiar fictional 555-123-4567,
        # and no observed failure needed it.
        raise ValueError(
            f"{field_name} is not a U.S. number — area codes never start with {digits[0]}"
        )
    return digits


def _state_for_zip(zip_code: str) -> Optional[str]:
    """The state a ZIP provably belongs to, or None if the prefix isn't mapped."""
    prefix = int(zip_code[:3])
    for low, high, state in ZIP_PREFIX_RANGES:
        if low <= prefix <= high:
            return state
    return None


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


def _check_zip_matches_state(state: Optional[str], zip_code: Optional[str]):
    """Reject a ZIP that belongs to a different state than the caller gave.

    Live calls produced "Chicago, Illinois, 75050" and "New York, 79234" — both
    Texas ZIPs, both saved without complaint. Either the city/state or the ZIP
    was misheard; the record is wrong regardless, so it's worth one more
    question on the call.
    """
    if not state or not zip_code:
        return
    actual = _state_for_zip(zip_code)
    if actual and actual != state:
        raise ValueError(
            f"zip_code {zip_code} is in {actual}, not {state} — "
            "please confirm the ZIP code and the state"
        )


class PatientCreate(PatientBase):
    """Everything the voice agent / POST /patients must supply.

    Carries the strict U.S.-specific rules that apply on write but not on read
    (see _digits_only): a record entering the system must look like a real U.S.
    patient, while records already stored stay readable regardless.
    """

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        return _digits_only(v, "phone_number", strict=True)

    @field_validator("emergency_contact_phone")
    @classmethod
    def _validate_emergency_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        return _digits_only(v, "emergency_contact_phone", strict=True)

    @model_validator(mode="after")
    def _validate_zip_matches_state(self):
        _check_zip_matches_state(self.state, self.zip_code)
        return self


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
        return _digits_only(v, "phone_number", strict=True) if v is not None else v

    @field_validator("emergency_contact_phone")
    @classmethod
    def _validate_emergency_phone(cls, v: Optional[str]) -> Optional[str]:
        return _digits_only(v, "emergency_contact_phone", strict=True) if v else None

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

    @model_validator(mode="after")
    def _validate_zip_matches_state(self):
        # Only meaningful when the update supplies both; a ZIP-only update
        # can't be cross-checked without reading the stored row.
        _check_zip_matches_state(self.state, self.zip_code)
        return self


class PatientOut(PatientBase):
    model_config = ConfigDict(from_attributes=True)

    patient_id: str
    created_at: datetime
    updated_at: datetime


class Envelope(BaseModel):
    """Every API response — success or error — shares this shape, per PDF spec."""
    data: Optional[object] = None
    error: Optional[str] = None
