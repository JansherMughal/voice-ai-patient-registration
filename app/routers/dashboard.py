"""Single-page HTML dashboard listing registered patients. No JS framework —
the server renders a table straight from the DB, reusing the same service layer."""
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import services
from app.db import get_db

router = APIRouter(tags=["dashboard"])

_ROW = """<tr><td>{first_name} {last_name}</td><td>{date_of_birth}</td><td>{sex}</td>
<td>{phone_number}</td><td>{city}, {state} {zip_code}</td><td>{created_at}</td></tr>"""

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Patient Registry</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #f7f8fa; color: #1a1a1a; }}
h1 {{ font-size: 1.4rem; }}
table {{ border-collapse: collapse; width: 100%; background: #fff; }}
th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #e2e2e2; font-size: 0.9rem; }}
th {{ background: #14213d; color: #fff; }}
tr:hover {{ background: #f0f4ff; }}
.count {{ color: #666; margin-bottom: 1rem; }}
</style></head><body>
<h1>Registered Patients</h1>
<p class="count">{count} patient(s)</p>
<table><thead><tr><th>Name</th><th>DOB</th><th>Sex</th><th>Phone</th><th>Location</th><th>Registered</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(db: Session = Depends(get_db)):
    patients = services.list_patients(db)
    rows = "".join(
        _ROW.format(
            first_name=p.first_name,
            last_name=p.last_name,
            date_of_birth=p.date_of_birth,
            sex=p.sex,
            phone_number=p.phone_number,
            city=p.city,
            state=p.state,
            zip_code=p.zip_code,
            created_at=p.created_at.strftime("%Y-%m-%d %H:%M UTC"),
        )
        for p in patients
    )
    return _PAGE.format(count=len(patients), rows=rows or "<tr><td colspan=6>No patients yet.</td></tr>")
