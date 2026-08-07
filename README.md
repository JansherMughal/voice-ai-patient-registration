# Voice AI Agent — Patient Registration

A voice AI agent (reachable at a real US phone number) that conversationally
registers new patients, persists them to Postgres, and exposes the records
through a REST API + dashboard.

**Phone number:** _fill in after Vapi provisioning_
**API base URL:** _fill in after Railway deploy_
**Dashboard:** `{API_BASE_URL}/dashboard`

## Architecture

```
Caller ──PSTN──> Vapi (Twilio number, Deepgram STT, GPT-4o, 11labs TTS)
                   │  tool calls over HTTPS, secret-verified
                   ▼
            FastAPI (Railway) ────> PostgreSQL (Railway managed)
                   │
                   ├── REST API   /patients   (CRUD, used by reviewers + Vapi)
                   ├── /vapi/webhook          (tool calls + end-of-call transcript)
                   └── /dashboard             (server-rendered patient list)
```

**Separation of concerns:**
- **Telephony/voice** — Vapi owns the phone number, speech-to-text, LLM turn-taking,
  and text-to-speech. It only knows how to *talk*; it has no direct DB access.
- **Conversation design** — `prompts/system_prompt.md`, the assistant's system prompt.
- **Tool contract** — `vapi/assistant.json` defines `lookup_patient`, `register_patient`,
  `update_patient` as callable functions with a JSON-schema matching the Pydantic model.
- **API/data layer** — FastAPI app in `app/`. `app/services.py` is the *single* place
  create/read/update/delete logic lives; both the public REST router
  (`app/routers/patients.py`) and the Vapi webhook (`app/routers/vapi.py`) call it,
  so a voice-collected record and a curl'd record go through identical validation.
- **Persistence** — SQLAlchemy models (`app/models.py`) on Postgres (Railway) in
  production, SQLite locally/in tests — same code path, different `DATABASE_URL`.

## Why this stack

- **Vapi** over building STT/TTS/turn-taking from scratch: the PDF explicitly says this
  is the fastest path to a *working* system, and grading rewards a working voice
  experience over a hand-rolled telephony pipeline. Vapi's function-calling maps
  directly onto `register_patient`/`update_patient` as tools.
- **FastAPI + Pydantic**: Pydantic validators give server-side validation "for free" —
  the PDF requires the API not rely solely on the voice agent for validation, so the
  exact same `PatientCreate`/`PatientUpdate` models validate both entry points, and
  invalid input becomes a 422 with a field-level message the agent can read back to
  the caller ("that date doesn't look right — could you give me your DOB again?").
- **Railway + Postgres**: one deploy target for both the app and a managed, persistent
  Postgres instance — satisfies "data must survive server restarts" with no extra infra.
- **SQLite fallback locally**: `DATABASE_URL` unset → `sqlite:///./patients.db`, so the
  whole thing runs and tests pass with zero external dependencies during development.

## Data model

See `app/models.py` for the full schema (all fields/constraints from the assessment
spec — required vs. optional, types, `patient_id` UUID PK, `deleted_at` for soft
delete, auto `created_at`/`updated_at`). `app/schemas.py` documents every validation
rule (name regex, DOB not in future, 10-digit phone normalization, 2-letter state
whitelist, ZIP/ZIP+4 regex, enum for `sex`) — this is the server-side enforcement
layer, independent of whatever the voice agent thinks it heard.

## REST API

All responses use the envelope `{"data": ..., "error": null}`.

| Method | Endpoint | Notes |
|---|---|---|
| GET | `/patients` | filters: `?last_name=`, `?date_of_birth=`, `?phone_number=` |
| GET | `/patients/{id}` | 404 if missing or soft-deleted |
| POST | `/patients` | 201 on success, 422 with field message on invalid input |
| PUT | `/patients/{id}` | partial update — only fields provided are changed |
| DELETE | `/patients/{id}` | soft delete (`deleted_at` set, row kept) |
| GET | `/dashboard` | bonus HTML patient list |
| POST | `/vapi/webhook` | Vapi tool-calls + end-of-call-report, secret-verified |
| GET | `/health` | liveness check |

Errors: 404 (not found), 422 (validation, from Pydantic), 500 (unhandled, logged
server-side, generic message returned — no stack traces leaked to the caller).

## Voice agent design

Full prompt + rationale in `prompts/system_prompt.md`. Summary of the conversation
flow: greet → collect phone number first (enables `lookup_patient` duplicate
detection before re-collecting a returning caller's info) → required fields one at a
time, with letter-by-letter name confirmation → offer optional fields once, opt-in
only → read back everything and get explicit confirmation → save → speak the result
(success, field-specific validation error, or "please call back" on a system error —
**never silence**).

### Tool calls (`vapi/assistant.json`)
- `lookup_patient(phone_number)` — duplicate-call detection.
- `register_patient({...})` — maps 1:1 onto `PatientCreate`; a Pydantic validation
  failure comes back as `validation_error|<field>: <message>` so the agent re-prompts
  that exact field instead of restarting the call.
- `update_patient(patient_id, {...})` — partial update for returning callers.

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # or .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp .env.example .env            # DATABASE_URL optional locally (defaults to SQLite)
uvicorn app.main:app --reload
```

Tables are created and 2 seed patients inserted automatically on startup if the
`patients` table is empty. Visit `http://127.0.0.1:8000/dashboard` or
`http://127.0.0.1:8000/docs` (auto-generated OpenAPI UI).

### Tests

```bash
pytest tests/ -v
```

Runs the full CRUD + validation surface against an isolated in-memory SQLite DB —
no network, no external services required.

## Deployment (Railway)

1. Push repo to GitHub.
2. New Railway project → "Deploy from GitHub repo" → add a Postgres plugin to the
   same project (Railway injects `DATABASE_URL` automatically — rename to match if
   needed).
3. Set `VAPI_WEBHOOK_SECRET` in Railway's environment variables.
4. Railway runs `Procfile`'s `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
5. Note the generated public URL — needed for the Vapi `serverUrl`.

## Vapi setup

1. Create an assistant, import `vapi/assistant.json` as a starting point (or set each
   field manually in the dashboard).
2. Paste the code block from `prompts/system_prompt.md` as the system prompt.
3. Set `serverUrl` to `https://<railway-app>/vapi/webhook` and `serverUrlSecret` to
   the same value as `VAPI_WEBHOOK_SECRET`.
4. Attach a free Vapi phone number (or buy a Twilio number and import it).
5. Test with Vapi's web-call feature first (no telephony cost), then call the real
   number.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | No (defaults to local SQLite) | Postgres connection string on Railway |
| `VAPI_WEBHOOK_SECRET` | Yes for voice integration | Verifies `/vapi/webhook` requests came from Vapi |

No API keys are hardcoded anywhere in source; OpenAI/Deepgram/11labs keys live only
inside the Vapi dashboard, never in this repo.

## Known limitations / trade-offs

- **In-memory call→patient map** (`app/routers/vapi.py`) links an end-of-call
  transcript to the patient it just registered. This resets if the process restarts
  mid-call and doesn't scale past a single instance — acceptable for a single-dyno
  take-home deploy; a production version would pass `patient_id` through Vapi's call
  metadata instead.
- **No call recording**, only transcript + summary text (bonus scope, not required).
- **No multi-language switching implemented** — the prompt notes `preferred_language`
  as a field to collect, but the agent doesn't auto-switch languages mid-call.
- **No appointment scheduling bonus.**
- **Soft-delete only** — `DELETE` never removes rows, per spec; no admin endpoint to
  hard-delete/purge.
- **No auth on the REST API itself** (only the Vapi webhook is secret-verified) —
  intentional for reviewer convenience; would add API-key auth for anything beyond a
  take-home.

## Next steps (if continuing past the time-box)

- Move the call→patient linkage into Vapi's call metadata instead of an in-process dict.
- Add multi-language support (detect "hablo español" → switch `transcriber.language`
  and prompt language mid-call via Vapi's dynamic variables).
- Add appointment scheduling as a fourth tool call against mock slot data.
- Add basic API-key auth to the public REST endpoints.
