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
    "zip_code": "10001",
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


@pytest.mark.parametrize(
    "spoken,zip_code,expected",
    [("New York", "10001", "NY"), ("texas", "75050", "TX"), ("ny", "10001", "NY")],
)
def test_state_names_map_to_abbreviations(spoken, zip_code, expected):
    assert make(state=spoken, zip_code=zip_code).state == expected


def test_unknown_state_rejected():
    with pytest.raises(ValidationError, match="state"):
        make(state="Narnia")


def test_zip_spoken_as_separate_digits():
    assert make(zip_code="1 0 0 0 1").zip_code == "10001"


@pytest.mark.parametrize("bad", ["0345329998", "0986567893", "1234567890"])
def test_non_us_numbers_rejected_even_when_ten_digits(bad):
    """Live calls dictated Pakistani numbers; ten digits is not enough to be a
    U.S. number. NANP area codes never begin with 0 or 1."""
    with pytest.raises(ValidationError, match="phone_number"):
        make(phone_number=bad)


def test_zip_belonging_to_another_state_is_rejected():
    """Three live registrations stored Texas ZIPs against NY and IL."""
    with pytest.raises(ValidationError, match="zip_code"):
        make(city="Chicago", state="Illinois", zip_code="75050")


def test_zip_matching_its_state_is_accepted():
    assert make(state="Texas", zip_code="75050", city="Grand Prairie").state == "TX"


def test_unmapped_zip_prefix_passes_rather_than_rejecting():
    """The prefix table is deliberately incomplete — a gap must never reject a
    legitimate address, so unknown prefixes are allowed through."""
    assert make(state="NY", zip_code="00501", city="Holtsville").zip_code == "00501"


def test_reads_stay_lenient_so_rows_saved_before_these_rules_remain_readable():
    from app.schemas import PatientOut

    stored = {
        **BASE,
        "phone_number": "0345329998",   # would be rejected on write now
        "state": "NY",
        "zip_code": "75050",  # Texas ZIP against NY, would be rejected on write now
        "patient_id": "abc",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    assert PatientOut(**stored).phone_number == "0345329998"


def test_update_schema_normalizes_the_same_way():
    upd = PatientUpdate(sex="Mail", state="New York", phone_number="double 2 double 3 445566")
    assert (upd.sex.value, upd.state, upd.phone_number) == ("Male", "NY", "2233445566")
