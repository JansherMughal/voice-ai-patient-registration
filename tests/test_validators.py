"""Server-side normalization of what the transcriber actually hands us.

Every case here came out of a real Vapi call log or is one keystroke away
from one ("double 1" read as a single 1, "1888" accepted as a birth year).
"""
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas import PatientCreate, PatientUpdate

BASE = {
    "first_name": "John",
    "last_name": "Butler",
    "date_of_birth": "1988-06-25",
    "sex": "Male",
    "phone_number": "3211231231",
    "address_line_1": "2 Bovat Street",
    "city": "New York City",
    "state": "NY",
    "zip_code": "75523",
}


def make(**overrides):
    return PatientCreate(**{**BASE, **overrides})


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("double 2 double 3 445566", "2233445566"),
        ("triple five 1234567", "5551234567"),
        ("five five five one two three four five six seven", "5551234567"),
        ("(555) 123-4567", "5551234567"),
        ("+1 555 123 4567", "5551234567"),  # leading country code stripped
        ("five five five oh one two three four five six", "5550123456"),  # "oh" is zero
    ],
)
def test_spoken_phone_numbers_become_ten_digits(spoken, expected):
    assert make(phone_number=spoken).phone_number == expected


def test_short_phone_number_is_rejected_not_padded():
    with pytest.raises(ValidationError, match="phone_number"):
        make(phone_number="double 1 2345")


@pytest.mark.parametrize("dob", ["1888-06-25", "1700-01-01", "2099-01-01"])
def test_implausible_birth_years_rejected(dob):
    with pytest.raises(ValidationError, match="date_of_birth"):
        make(date_of_birth=dob)


def test_plausible_old_patient_still_accepted():
    assert make(date_of_birth="1930-04-02").date_of_birth == date(1930, 4, 2)


@pytest.mark.parametrize(
    "heard,expected",
    [("Mail", "Male"), ("mayle", "Male"), ("femail", "Female"),
     ("prefer not to say", "Decline to Answer"), ("non-binary", "Other")],
)
def test_sex_homophones_normalized(heard, expected):
    assert make(sex=heard).sex.value == expected


@pytest.mark.parametrize("spoken,expected", [("New York", "NY"), ("texas", "TX"), ("ny", "NY")])
def test_state_names_map_to_abbreviations(spoken, expected):
    assert make(state=spoken).state == expected


def test_unknown_state_rejected():
    with pytest.raises(ValidationError, match="state"):
        make(state="Narnia")


def test_zip_spoken_as_separate_digits():
    assert make(zip_code="7 5 5 2 3").zip_code == "75523"


def test_update_schema_normalizes_the_same_way():
    upd = PatientUpdate(sex="Mail", state="New York", phone_number="double 1 22334455")
    assert (upd.sex.value, upd.state, upd.phone_number) == ("Male", "NY", "1122334455")
