"""Run a SQL statement against whatever DATABASE_URL is in the environment.

Avoids needing a local psql install: `railway run` injects the production
DATABASE_URL, and psycopg is already a dependency.

    railway run python scripts/dbshell.py "select count(*) from patients"
    railway run python scripts/dbshell.py            # defaults to listing patients
"""
import os
import sys

import psycopg

DEFAULT = "select patient_id, first_name, last_name, phone_number, created_at from patients order by created_at desc limit 20"

sql = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
# DATABASE_URL points at postgres.railway.internal, which only resolves inside
# Railway's network. From a laptop we need the proxied public URL instead.
url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
if not url:
    sys.exit("No DATABASE_URL — run this via `railway run`")

with psycopg.connect(url) as conn, conn.cursor() as cur:
    cur.execute(sql)
    if cur.description is None:
        print(f"ok, {cur.rowcount} row(s) affected")
    else:
        print(" | ".join(c.name for c in cur.description))
        for row in cur.fetchall():
            print(" | ".join(str(v) for v in row))
