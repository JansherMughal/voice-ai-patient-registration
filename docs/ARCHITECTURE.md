# Architecture

Technical reference for the voice AI patient registration system. Every diagram
below describes code that exists in this repository — file and line references
are given so each claim can be checked against the source.

---

## 1. System context

Who talks to what, and over which protocol.

```mermaid
graph LR
    caller["Caller<br/>(US phone)"]

    subgraph vapi["Vapi (managed voice pipeline)"]
        pstn["Telephony<br/>+1 716 513 2013"]
        stt["Deepgram nova-3<br/>speech to text"]
        llm["OpenAI GPT-4.1<br/>temp 0.4"]
        tts["ElevenLabs turbo_v2_5<br/>text to speech"]
    end

    subgraph railway["Railway"]
        api["FastAPI<br/>app/main.py"]
        db[("PostgreSQL<br/>patients<br/>call_transcripts")]
    end

    reviewer["Reviewer<br/>(curl / browser)"]

    caller <-->|"PSTN audio"| pstn
    pstn --> stt --> llm --> tts --> pstn
    llm -->|"tool calls<br/>x-vapi-secret"| api
    api -->|"results"| llm
    api --- db
    reviewer -->|"REST + /dashboard"| api
```

**Why a managed voice platform.** The assessment scores integration and system
design, not STT/TTS implementation. Vapi owns the realtime audio loop; this
repository owns the parts that are actually being evaluated — conversation
design, validation, persistence, and the API.

---

## 2. Layering and separation of concerns

Two entry points, one service layer, one set of validators. Nothing reaches the
database without passing through both.

```mermaid
graph TD
    subgraph entry["Entry points"]
        rest["REST router<br/>app/routers/patients.py"]
        hook["Vapi webhook<br/>app/routers/vapi.py"]
        dash["Dashboard<br/>app/routers/dashboard.py"]
    end

    subgraph validation["Validation"]
        schemas["Pydantic schemas<br/>app/schemas.py"]
    end

    subgraph domain["Domain"]
        services["Service layer<br/>app/services.py"]
    end

    subgraph persistence["Persistence"]
        models["ORM models<br/>app/models.py"]
        engine["Engine + session<br/>app/db.py"]
    end

    rest --> schemas
    hook --> schemas
    schemas --> services
    dash --> services
    services --> models --> engine

    style validation fill:#fff4e6
    style domain fill:#e8f4ff
```

**The decision worth pointing at:** the voice agent does not call the public
REST API over HTTP. Both entry points import the same functions from
[`app/services.py`](../app/services.py). The PDF permits either
("use the REST API *or directly invoke the same service layer*"); calling the
service layer directly removes a network hop and, more importantly, makes it
structurally impossible for voice-path validation to drift from API-path
validation.

---

## 3. Registration call — end to end

Happy path for a new patient, from dial tone to persisted row.

```mermaid
sequenceDiagram
    autonumber
    actor C as Caller
    participant V as Vapi pipeline
    participant W as POST /vapi/webhook
    participant S as services.py
    participant DB as PostgreSQL

    C->>V: dials number
    V-->>C: "I'm Ava... can I start with your phone number?"

    C->>V: "double two, double three, four four five five six six"
    V->>W: tool-calls: lookup_patient
    W->>W: verify x-vapi-secret
    W->>S: find_by_phone("2233445566")
    S->>DB: SELECT ... WHERE phone_number = ? AND deleted_at IS NULL
    DB-->>S: no row
    S-->>W: None
    W-->>V: "no_existing_patient"

    loop required fields, one at a time
        V-->>C: asks one question
        C->>V: answers
    end

    V-->>C: offers optional fields (insurance, emergency contact, language)
    V-->>C: reads every field back
    C->>V: "yes, that's right"

    V->>W: tool-calls: register_patient {16 fields}
    W->>W: PatientCreate(**args) — normalize + validate

    alt validation fails
        W-->>V: "validation_error|date_of_birth: ...age over 120..."
        V-->>C: re-prompts that one field only
    else valid
        W->>S: create_patient(payload)
        S->>DB: INSERT
        DB-->>S: patient_id
        W->>W: _call_patient_map[call_id] = patient_id
        W-->>V: "success|<uuid>|Jane"
        V-->>C: "You're all set, Jane."
        V->>V: end_call tool
    end

    V->>W: end-of-call-report {transcript, summary}
    W->>DB: INSERT call_transcripts (linked to patient_id)
```

Steps 20–21 are the transcript bonus: `end-of-call-report` carries only the Vapi
call id, so [`app/routers/vapi.py:34`](../app/routers/vapi.py#L34) keeps a
`call_id -> patient_id` map populated during the tool call, letting the stored
transcript join back to the patient it created.

---

## 4. Returning caller — duplicate detection

The bonus challenge. Phone number is collected **first**, not last, precisely so
this branch can be taken before wasting the caller's time.

```mermaid
sequenceDiagram
    autonumber
    actor C as Caller
    participant V as Vapi pipeline
    participant W as POST /vapi/webhook
    participant S as services.py

    C->>V: gives phone number
    V->>W: lookup_patient
    W->>S: find_by_phone(digits)
    S-->>W: Patient(Jane Doe)
    W-->>V: "existing_patient_found|<uuid>|Jane|Doe"
    V-->>C: "We already have a record for Jane Doe.<br/>Update instead?"

    alt caller says yes
        C->>V: "yes, change my ZIP"
        V-->>C: collects only changed fields
        V->>W: update_patient {patient_id, zip_code}
        W->>S: update_patient(patient, PatientUpdate)
        Note over S: model_dump(exclude_unset=True)<br/>only supplied fields are written
        W-->>V: "success|<uuid>|Jane"
    else caller says no
        V-->>C: continues as a new registration
    end
```

`exclude_unset=True` in [`app/services.py:63`](../app/services.py#L63) is what
makes partial updates safe: an omitted field stays untouched rather than being
overwritten with `None`.

---

## 5. Validation pipeline

The requirement is *"validate all inputs server-side (do not rely solely on the
voice agent for validation)"*. Everything below runs inside Pydantic, on both
entry points, before any SQL is issued.

```mermaid
flowchart TD
    raw["Raw tool arguments<br/>from the LLM"] --> sex

    sex["sex<br/>mode=before"] -->|"'Mail' / 'femail' /<br/>'prefer not to say'"| sexmap["SEX_ALIASES lookup"]
    sexmap --> enum{"matches<br/>Sex enum?"}
    enum -->|no| err["ValidationError"]
    enum -->|yes| phone

    phone["phone_number"] --> spoken["_spoken_to_digits()"]
    spoken --> dbl["expand double/triple<br/>map word digits + 'oh'"]
    dbl --> cc["strip leading country code 1"]
    cc --> len{"exactly<br/>10 digits?"}
    len -->|no| err
    len -->|yes| dob

    dob["date_of_birth"] --> fut{"in the<br/>future?"}
    fut -->|yes| err
    fut -->|no| age{"age over<br/>120?"}
    age -->|yes| err
    age -->|no| state

    state["state"] --> sname{"full state<br/>name?"}
    sname -->|"'New York'"| abbr["map to 'NY'"]
    sname -->|no| upper["uppercase"]
    abbr --> known{"valid<br/>US state?"}
    upper --> known
    known -->|no| err
    known -->|yes| zip

    zip["zip_code"] --> strip["strip spoken spacing<br/>'7 5 5 2 3' → '75523'"]
    strip --> zre{"matches<br/>ZIP / ZIP+4?"}
    zre -->|no| err
    zre -->|yes| ok["services.create_patient()"]

    err --> msg["_format_validation_error()<br/>one speakable sentence"]
    msg --> reprompt["'validation_error|field: message'<br/>agent re-prompts that field only"]

    style err fill:#ffe6e6
    style ok fill:#e6ffe9
```

Each normalizer exists because a real call produced the failure it prevents:

| Heard on a live call | Naive result | Handled by |
|---|---|---|
| "double 1" | `1` | `_spoken_to_digits()` — [schemas.py:59](../app/schemas.py#L59) |
| "March fourth, eighteen ninety" | accepted, age 136 | `_check_dob()` — [schemas.py:108](../app/schemas.py#L108) |
| "Mail" | enum rejection | `SEX_ALIASES` — [schemas.py:36](../app/schemas.py#L36) |
| "New York" | rejected, wanted `NY` | `_normalize_state()` — [schemas.py:96](../app/schemas.py#L96) |
| "seven five five two three" | `"7 5 5 2 3"` | zip validator — [schemas.py:170](../app/schemas.py#L170) |

**The engineering point:** the LLM is treated as an untrusted input source. It
mishears, and its mistakes are systematic rather than random, so the server
corrects the systematic ones and rejects the rest with a message the agent can
read aloud.

---

## 6. Conversation state machine

The prompt in [`prompts/system_prompt.md`](../prompts/system_prompt.md) is
structured as an explicit flow rather than a wall of instructions.

```mermaid
stateDiagram-v2
    [*] --> Greet
    Greet --> GetPhone: ask phone number first

    GetPhone --> Lookup: 10 digits confirmed
    GetPhone --> GetPhone: fewer than 10 — ask for the rest

    Lookup --> UpdateMode: existing_patient_found
    Lookup --> Required: no_existing_patient

    Required --> Required: one field per turn
    Required --> Required: validation_error — re-prompt that field
    Required --> Optional: all required fields collected

    Optional --> Confirm: offered once, caller opts in or declines
    UpdateMode --> Confirm: changed fields only

    Confirm --> Required: caller corrects a field
    Confirm --> Save: caller confirms

    Save --> Success: success|...
    Save --> Confirm: validation_error
    Save --> Failure: error|...

    Success --> EndCall
    Failure --> EndCall: "try calling back shortly"
    EndCall --> [*]
```

Two constraints encoded in the prompt map directly to evaluation criteria:

- **A correction returns to the field, not to the start.** The PDF asks what
  happens when a caller says *"actually my last name is D-A-V-I-S"*; the
  `Confirm → Required` edge is that answer.
- **`Failure` still reaches `EndCall` through speech.** The PDF asks whether a
  failed database write produces *"an error or silence"*. Every terminal state
  says something before hanging up.

---

## 7. Data model

```mermaid
erDiagram
    PATIENTS ||--o{ CALL_TRANSCRIPTS : "produced by"

    PATIENTS {
        string patient_id PK "UUID, String(36)"
        string first_name "50, not null"
        string last_name "50, not null"
        date date_of_birth "not null"
        string sex "20, not null"
        string phone_number "10, not null, indexed"
        string email "255, null"
        string address_line_1 "255, not null"
        string address_line_2 "255, null"
        string city "100, not null"
        string state "2, not null"
        string zip_code "10, not null"
        string insurance_provider "150, null"
        string insurance_member_id "50, null"
        string preferred_language "50, default English"
        string emergency_contact_name "150, null"
        string emergency_contact_phone "10, null"
        datetime created_at "UTC, auto"
        datetime updated_at "UTC, auto on modify"
        datetime deleted_at "null until soft delete"
    }

    CALL_TRANSCRIPTS {
        string id PK "UUID"
        string patient_id FK "nullable"
        string vapi_call_id "indexed"
        text transcript
        text summary
        datetime created_at "UTC"
    }
```

Three schema decisions worth defending in review:

1. **`patient_id` is `String(36)`, not a native `UUID` column.** The same model
   runs unmodified on SQLite (tests, local) and PostgreSQL (Railway). A native
   UUID type would have forced a dialect branch for no functional gain.
2. **`phone_number` is `String(10)` and indexed.** Digits only, no formatting —
   so `find_by_phone` is an exact-match index hit rather than a `LIKE` scan, and
   `(555) 123-4567` and `5551234567` cannot become two different patients.
3. **`sex` is `String(20)`, not a DB enum.** The allowed values live in the
   Pydantic `Sex` enum. A database enum would require a migration to add a
   value, and both write paths already pass through Pydantic.
4. **`deleted_at` rather than `DELETE`.** The PDF requires soft deletes; every
   read path filters `deleted_at IS NULL`
   ([services.py:30](../app/services.py#L30), [51](../app/services.py#L51)).

---

## 8. Request lifecycle and error envelope

Every response — success or failure — has the same shape.

```mermaid
flowchart TD
    req["HTTP request"] --> route{"route"}
    route --> handler["router handler"]
    handler --> pyd{"Pydantic<br/>validates?"}

    pyd -->|no| v422["RequestValidationError"]
    pyd -->|yes| svc["services.*"]

    svc --> found{"row<br/>found?"}
    found -->|no| h404["HTTPException 404"]
    found -->|yes| ok200["Envelope(data=...)"]

    svc --> boom["unexpected exception"]

    v422 --> eh422["validation_exception_handler<br/>main.py:28"]
    h404 --> eh404["http_exception_handler<br/>main.py:22"]
    boom --> eh500["unhandled_exception_handler<br/>main.py:35"]

    eh422 --> out422["422 {data:null, error:'field: msg'}"]
    eh404 --> out404["404 {data:null, error:'patient not found'}"]
    eh500 --> out500["500 {data:null, error:'internal server error'}"]
    ok200 --> out200["200/201 {data:{...}, error:null}"]

    style out200 fill:#e6ffe9
    style out422 fill:#fff4e6
    style out404 fill:#fff4e6
    style out500 fill:#ffe6e6
```

The three exception handlers in [`app/main.py:22-38`](../app/main.py#L22-L38)
exist so the envelope holds on error paths too — FastAPI's defaults would
otherwise return a bare `{"detail": ...}` and break the contract the PDF
specifies. The 500 handler deliberately returns a generic message while logging
the traceback: internal detail belongs in logs, not in responses.

---

## 9. Deployment and configuration

```mermaid
graph TD
    dev["Local dev<br/>SQLite, .venv"]
    gh["GitHub<br/>master"]

    subgraph rw["Railway project"]
        build["Railpack build<br/>.python-version 3.12<br/>requirements.txt"]
        run["uvicorn app.main:app<br/>Procfile"]
        pg[("Postgres service<br/>postgres-volume")]
    end

    subgraph vapicfg["Vapi configuration"]
        assistant["Assistant 'Ava'"]
        tools["4 published tools"]
    end

    dev -->|"git push"| gh
    gh -->|"auto-deploy on push"| build --> run
    run -->|"DATABASE_URL<br/>(reference variable)"| pg
    tools -->|"Server URL +<br/>x-vapi-secret header"| run

    dev -->|"vapi/apply-assistant.ps1<br/>PATCH /assistant/:id"| assistant
    assistant --- tools
```

**Configuration as code.** The assistant's settings live in
[`vapi/assistant.json`](../vapi/assistant.json) and the prompt in
[`prompts/system_prompt.md`](../prompts/system_prompt.md);
[`vapi/apply-assistant.ps1`](../vapi/apply-assistant.ps1) pushes both to the
live assistant over the Vapi REST API. Voice configuration is version-controlled
and reviewable in a diff rather than existing only as dashboard state.

**Secrets.** `DATABASE_URL` and `VAPI_WEBHOOK_SECRET` are environment variables
([`app/config.py`](../app/config.py)); nothing is hardcoded. Every webhook
request is rejected unless `x-vapi-secret` matches
([`vapi.py:37`](../app/routers/vapi.py#L37)) — without it, the endpoint would
let anyone on the internet write patient rows.

**Driver choice.** `psycopg2` links `libpq` dynamically and the deploy image
does not ship it, so [`app/db.py`](../app/db.py) rewrites the connection scheme
to `postgresql+psycopg` and the project uses `psycopg[binary]`, whose wheels
bundle `libpq`. This removes the system dependency instead of working around it.

---

## 10. Voice pipeline tuning

Settings that exist because a specific live-call failure motivated them.

```mermaid
graph LR
    subgraph listen["Deciding the caller has finished"]
        punct["onPunctuation 0.2s"]
        nopunct["onNoPunctuation 1.4s"]
        num["onNumber 1.2s"]
        smart["smartEndpointing on"]
    end

    subgraph interrupt["Being interrupted"]
        words["numWords 2"]
        vs["voiceSeconds 0.3"]
        backoff["backoffSeconds 1.5"]
    end

    subgraph silence["Caller goes quiet"]
        idle["idle nudge every 20s"]
        max3["up to 3 nudges"]
        hang["hang up at 90s"]
    end
```

`onNumberSeconds` is the one that mattered most. Callers pause between digit
groups — *"triple five... one two three... four five six seven"* — and at the
default endpointing threshold the agent treated each pause as end-of-turn and
interrupted, which is why an emergency contact number once took four attempts.

Silence handling follows the same reasoning: the default is a single 30-second
timeout with no warning, so a caller who paused to find their insurance card got
hung up on. Three spoken nudges before a 90-second cutoff replaces that.

---

## 11. Test strategy

```mermaid
graph TD
    conftest["tests/conftest.py<br/>in-memory SQLite + StaticPool<br/>dependency_overrides[get_db]"]

    conftest --> api["test_patients_api.py<br/>REST CRUD, envelope, 404, soft delete"]
    conftest --> hook["test_vapi_webhook.py<br/>tool-call contract, arg shapes,<br/>secret enforcement"]
    conftest --> val["test_validators.py<br/>spoken digits, age bound,<br/>homophones, state names, ZIP"]
```

`StaticPool` keeps one connection alive so `:memory:` survives across requests
inside a test; an autouse fixture creates and drops tables per test so ordering
cannot leak state. Fixtures live in `conftest.py` specifically because
per-module setup previously caused one module to drop another's tables.

The validator tests are the ones worth reading — each case is a transcription
failure observed on a real call, not a hypothetical.

---

## 12. Known limitations

| Limitation | Consequence | What would change it |
|---|---|---|
| `_call_patient_map` is an in-process dict ([vapi.py:34](../app/routers/vapi.py#L34)) | Transcript↔patient linking breaks across multiple replicas or a restart mid-call | Persist the mapping, or key transcripts by `vapi_call_id` and reconcile later |
| No database migrations | Schema changes rely on `create_all` | Alembic |
| Seed data runs on every startup ([main.py:41](../app/main.py#L41)) | Only guarded by an emptiness check | Move to an explicit seed command |
| Deepgram still mishears spelled letters | Phonetic spelling ("D as in David") mitigates but does not eliminate | Constrained decoding or DTMF entry for critical fields |
| No auth on the REST API | Anyone with the URL can read patient rows | The PDF scopes HIPAA out; production would need auth |
