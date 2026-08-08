# Voice AI Agent — Patient Registration

A voice AI agent, reachable at a real US phone number, that conversationally
registers new patients, persists them to Postgres, and exposes the records
through a REST API and dashboard.

| | |
|---|---|
| **Phone number** | **+1 (716) 513-2013** — call it and register |
| **API base URL** | https://voice-ai-patient-registration-production-2d88.up.railway.app |
| **Dashboard** | [`/dashboard`](https://voice-ai-patient-registration-production-2d88.up.railway.app/dashboard) |
| **API docs** | [`/docs`](https://voice-ai-patient-registration-production-2d88.up.railway.app/docs) |

No credentials needed to test — the REST API is open by design so reviewers can
query it directly. Only the Vapi webhook is secret-verified.

For diagrams of the call flow, validation pipeline, data model, and deployment
topology, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Architecture

```
Caller ──PSTN──> Vapi (phone number, Deepgram nova-3 STT, GPT-4.1, ElevenLabs TTS)
                   │  tool calls over HTTPS, secret-verified
                   ▼
            FastAPI (Railway) ────> PostgreSQL (Railway managed)
                   │
                   ├── /patients      REST CRUD (reviewers + voice agent)
                   ├── /vapi/webhook  tool calls + end-of-call transcript
                   ├── /transcripts   stored call transcripts
                   └── /dashboard     server-rendered patient list
```

**Separation of concerns**

- **Telephony/voice** — Vapi owns the phone number, speech-to-text, LLM
  turn-taking, and text-to-speech. It only knows how to *talk*; it has no
  database access.
- **Conversation design** — [`prompts/system_prompt.md`](prompts/system_prompt.md).
- **Tool contract** — [`vapi/assistant.json`](vapi/assistant.json) defines
  `lookup_patient`, `register_patient`, `update_patient`, and `end_call`.
- **API/data layer** — `app/`. [`app/services.py`](app/services.py) is the
  *single* place CRUD logic lives; both the REST router and the Vapi webhook
  call it, so a voice-collected record and a curl'd record pass identical
  validation.
- **Persistence** — SQLAlchemy models on Postgres in production, SQLite locally
  and in tests. Same code path, different `DATABASE_URL`.

## Why this stack

- **Vapi** over hand-building STT/TTS/turn-taking. Speech recognition and
  telephony are solved problems; the interesting work here is conversation
  design, validation, and persistence. Its function-calling maps directly onto
  the tools above.
- **FastAPI + Pydantic.** The spec requires server-side validation independent
  of the voice agent; the same `PatientCreate`/`PatientUpdate` models validate
  both entry points, and a failure becomes a field-level message the agent reads
  back to the caller.
- **Railway + Postgres.** One deploy target for app and managed database —
  satisfies "data must survive server restarts" with no extra infrastructure.
- **SQLite fallback.** `DATABASE_URL` unset → `sqlite:///./patients.db`, so the
  project runs and all tests pass with zero external dependencies.
- **psycopg3 over psycopg2.** psycopg2 links `libpq` dynamically and the deploy
  image doesn't ship it; psycopg3's wheels bundle it. [`app/db.py`](app/db.py)
  rewrites the connection scheme to `postgresql+psycopg`.

## Data model

Full schema in [`app/models.py`](app/models.py) — every field from the spec,
`patient_id` UUID primary key, `deleted_at` for soft delete, auto
`created_at`/`updated_at` in UTC, plus a `call_transcripts` table linked by
foreign key.

Three decisions worth noting:

- `patient_id` is `String(36)` rather than a native UUID column, so the same
  model runs unchanged on SQLite and Postgres.
- `phone_number` is stored as 10 digits, no formatting, and indexed — so
  duplicate detection is an exact index hit and `(555) 123-4567` cannot become a
  second patient alongside `5551234567`.
- `sex` is `String(20)` with the enum enforced in Pydantic, so adding a value
  doesn't require a database migration.

## Validation

[`app/schemas.py`](app/schemas.py) is the enforcement layer, and it treats the
LLM as an untrusted input source. Beyond the spec's rules (name regex, DOB not
in the future, 10-digit phone, 2-letter state, ZIP/ZIP+4, `sex` enum), it
normalizes the transcription failures that actually occurred on live calls:

| Heard on a real call | Naive result | Handled by |
|---|---|---|
| "double 1" | `1` | spoken-digit expansion, incl. "triple", word digits, "oh" |
| "March fourth, eighteen ninety" | accepted, age 136 | age bound of 120 years |
| "Mail" | enum rejection | homophone map (`mail`/`mayle`/`femail`) |
| "New York" | rejected, wanted `NY` | full state name → abbreviation |
| "seven five five two three" | `"7 5 5 2 3"` | spacing stripped before the regex |
| `0345329998` | accepted as US | NANP: area codes never start with 0 or 1 |
| "Chicago, Illinois, 75050" | saved | ZIP↔state cross-check (75050 is Texas) |

Two deliberate design choices in here:

1. **The ZIP prefix table fails open.** An unmapped prefix passes. A gap in the
   data can never reject a legitimate address — it can only miss a catch.
2. **Strict on write, lenient on read.** These rules run on `PatientCreate` and
   `PatientUpdate`, not on `PatientOut`. Rows saved before the rules existed stay
   retrievable; tightening validation must not make stored data unreadable.

## REST API

All responses use the envelope `{"data": ..., "error": null}`.

| Method | Endpoint | Notes |
|---|---|---|
| GET | `/patients` | filters: `?last_name=`, `?date_of_birth=`, `?phone_number=` |
| GET | `/patients/{id}` | 404 if missing or soft-deleted |
| POST | `/patients` | 201 on success, 422 with a field-level message |
| PUT | `/patients/{id}` | partial update — only supplied fields change |
| DELETE | `/patients/{id}` | soft delete (`deleted_at` set, row retained) |
| GET | `/transcripts` | stored call transcripts, `?patient_id=` to filter |
| GET | `/dashboard` | HTML patient list |
| POST | `/vapi/webhook` | Vapi tool-calls + end-of-call-report, secret-verified |
| GET | `/health` | liveness check |

Errors: 404 not found, 422 validation (Pydantic), 500 unhandled — logged
server-side with a generic message returned, no stack traces leaked. The
envelope is preserved on error paths by exception handlers in
[`app/main.py`](app/main.py); FastAPI's defaults would otherwise return a bare
`{"detail": ...}`.

Try it:

```bash
BASE=https://voice-ai-patient-registration-production-2d88.up.railway.app
curl -s $BASE/patients | python -m json.tool
curl -s "$BASE/patients?last_name=Doe" | python -m json.tool
curl -s $BASE/transcripts | python -m json.tool
```

## Voice agent design

Full prompt and rationale in [`prompts/system_prompt.md`](prompts/system_prompt.md).

Flow: greet → **phone number first** (so duplicate detection can fire before
re-collecting a returning caller's details) → required fields one at a time →
optional fields offered once, opt-in only → read everything back and get
explicit confirmation → save → speak the outcome → hang up.

### Tools

- `lookup_patient(phone_number)` — duplicate detection, **and digit counting**.
- `register_patient({...})` — maps 1:1 onto `PatientCreate`. A validation
  failure returns `validation_error|<field>: <message>` so the agent re-prompts
  that one field instead of restarting.
- `update_patient(patient_id, {...})` — partial update for returning callers.
- `end_call` — the agent hangs up itself after a successful save or a goodbye.

### Prompt engineering notes

The prompt is structured as an explicit five-step flow with rules derived from
observed failures rather than guesses. The most significant:

**Digit counting was moved out of the LLM.** Transcripts showed the model
calling the same 10-digit number "8 digits", then "9", then "11" — looping for
two minutes. Counting is a task LLMs are structurally bad at and the server does
exactly, so `lookup_patient` now counts and returns an instruction the model
only has to follow: `invalid_phone|so far I have 9 of 10 digits, ask for 1 more`.
The prompt forbids the model from counting or saying a count aloud.

Others, each traced to a specific call:

- **Never invent a value.** The agent heard garbled audio and asked "is your
  street Broadway?" — a word the caller never said, who then agreed to move on.
- **Two spelling attempts, then move on.** One call spent six rounds on
  D/B/P/T. Spelled single letters are the hardest thing for a phone line to
  carry; the agent now asks phonetically ("D as in David") and stops after two.
- **Never coach the caller** on how to say numbers — the parser handles
  "double"/"triple" silently.
- **Say each thing once.** The agent was reading a number back three times in
  one turn.

### Voice pipeline tuning

`onNumberSeconds: 1.2` is the setting that mattered most: callers pause between
digit groups, and at the default endpointing threshold the agent treated each
pause as end-of-turn and interrupted. Silence handling replaces Vapi's single
silent 30-second cutoff with three spoken nudges before hanging up at 90s.

### Configuration as code

[`vapi/assistant.json`](vapi/assistant.json) holds the assistant settings and
[`vapi/apply-assistant.ps1`](vapi/apply-assistant.ps1) pushes them, plus the
prompt, to the live assistant over the Vapi API:

```powershell
./vapi/apply-assistant.ps1   # reads VAPI_PRIVATE_KEY, VAPI_ASSISTANT_ID, VAPI_WEBHOOK_SECRET from env
```

Voice configuration is version-controlled and reviewable in a diff rather than
living only as dashboard state. Secrets stay in environment variables.

## Edge cases

| Scenario | Behaviour |
|---|---|
| Invalid date of birth | Field-level re-prompt: "that would make you over 120 — could you give me the year again?" |
| Caller declines a required field | Explains the record can't be created without it, asks again |
| Database write fails | Caller is told to call back shortly — never silence |
| Caller wants to start over | Everything collected is discarded, restarts from the phone number |
| Caller answers out of order | Value is stored and that question is skipped later |
| Call drops mid-registration | Nothing is written until confirmation, so no partial records |
| Caller goes silent | Three spoken nudges, then a graceful hang-up at 90s |
| Unauthenticated webhook POST | 401, no database access |

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # or .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # DATABASE_URL optional locally (defaults to SQLite)
uvicorn app.main:app --reload
```

Tables are created and two seed patients inserted on startup if the table is
empty. Visit `http://127.0.0.1:8000/dashboard` or `/docs`.

### Tests

```bash
pytest -q     # 48 tests
```

Runs against an isolated in-memory SQLite database — no network, no external
services. Coverage: REST CRUD and the envelope, the Vapi tool-call contract,
webhook secret enforcement, transcript linking, and every validator. The
validator cases are transcription failures observed on real calls, not
hypotheticals.

## Deployment (Railway)

1. Push to GitHub, create a Railway project from the repo, add a Postgres
   service. Set `DATABASE_URL` to `${{Postgres.DATABASE_URL}}`.
2. Set `VAPI_WEBHOOK_SECRET` to a random string.
3. Railway builds via `.python-version` + `requirements.txt` and runs the
   `Procfile`.
4. **Enable "Auto deploys when pushed to GitHub"** — off by default, and a stale
   build is indistinguishable from a broken one in the logs.

## Vapi setup

1. Create an assistant and provision a phone number (free Vapi numbers are
   inbound-only, which is all this needs).
2. Create the four tools. The three functions each need the Server URL
   `https://<railway-app>/vapi/webhook` and an `x-vapi-secret` header matching
   `VAPI_WEBHOOK_SECRET`; `end_call` is built in and needs neither.
3. Set the **assistant-level** Server URL to the same webhook with the same
   secret — `end-of-call-report` is delivered there, not to the tool URLs, so
   without it transcripts never arrive.
4. Run `./vapi/apply-assistant.ps1` to push the prompt and settings.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | No (defaults to SQLite) | Postgres connection string |
| `VAPI_WEBHOOK_SECRET` | Yes for voice | Verifies `/vapi/webhook` requests came from Vapi |

No API keys are hardcoded. OpenAI/Deepgram/ElevenLabs keys live only in the Vapi
dashboard, never in this repo.

## Observability

Tool calls and their arguments, every registration and update with the resulting
`patient_id`, and full tracebacks for unhandled errors are logged to stdout
(`railway logs`). Full call transcripts and summaries are persisted to the
`call_transcripts` table and readable at `/transcripts`.

## Bonus challenges

| Bonus | Status |
|---|---|
| Duplicate detection | **Done** — phone number collected first so it fires before re-collecting |
| Call transcript linked to the patient record | **Done** — stored and exposed at `/transcripts` |
| Dashboard | **Done** — `/dashboard` |
| Automated tests | **Done** — 48 tests |
| Appointment scheduling | Not implemented |
| Multi-language support | Not implemented — `preferred_language` is collected but the agent doesn't switch languages mid-call |

## Known limitations / trade-offs

- **In-memory call→patient map** ([`app/routers/vapi.py`](app/routers/vapi.py))
  links an end-of-call transcript to the patient just registered. Resets on
  restart and doesn't scale past one instance; a production version would pass
  `patient_id` through Vapi's call metadata.
- **No database migrations** — schema changes rely on `create_all`. Alembic
  would be the next step.
- **Spelled letters still get misheard.** Phonetic spelling mitigates it;
  Deepgram remains weakest on single letters over a phone line.
- **Seed data runs on every startup**, guarded only by an emptiness check.
- **No auth on the REST API** — deliberate, so reviewers can query it. The
  webhook is secret-verified.
- **Soft delete only**, per spec — no purge endpoint.
- **One NANP rule deliberately not enforced.** The standard also forbids the
  exchange starting with 0 or 1, which would reject the familiar fictional
  555-123-4567. No observed failure needed it, so it was left out.

## Next steps

- Move call→patient linkage into Vapi call metadata.
- Multi-language: detect "hablo español" and switch transcriber language and
  prompt mid-call.
- Appointment scheduling as a fifth tool against mock slot data.
- API-key auth on the public REST endpoints.
- Alembic migrations.
