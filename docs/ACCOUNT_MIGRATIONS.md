# Account migrations (CLI / API, no dashboard)

Steps used to move this app's infra — Railway hosting and, separately, the
Vapi voice assistant — to fresh accounts, without touching either dashboard
except to read a key.

## Railway account migration

Move the app to a fresh Railway account after a trial expired.

### 1. Switch Railway CLI to the target account

```powershell
railway logout
railway login
```

Browser opens — sign in with the account that should own the project.
Verify:

```powershell
railway whoami
```

(If using the Railway MCP server instead of raw CLI, same underlying auth —
`mcp__railway__whoami` reflects whichever account the CLI last logged into.)

### 2. Find the target project/service/environment IDs

```powershell
railway list          # or mcp__railway__list_projects
```

Then for the project:

```
mcp__railway__list_services      project_id=<id>
```

`list_services` / `list_variables` errors will list available environment
IDs if you omit `environment_id` — copy from the error message.

### 3. Provision Postgres

```
mcp__railway__deploy_template
  template_code=postgres
  project_id=<project_id>
  environment_id=<environment_id>
```

Poll until ready:

```
mcp__railway__environment_status  project_id=<id>  environment_id=<id>
```

### 4. Read the new Postgres connection string

```
mcp__railway__list_variables
  project_id=<project_id>
  service_id=<postgres_service_id>
  environment_id=<environment_id>
```

Grab `DATABASE_URL` (internal form, `postgres.railway.internal`, only
reachable from services in the same project).

### 5. Set env vars on the app service

```
mcp__railway__set_variables
  project_id=<project_id>
  service_id=<app_service_id>
  environment_id=<environment_id>
  variables={ "DATABASE_URL": "<from step 4>", "VAPI_WEBHOOK_SECRET": "<reuse from old project, see .env.example>" }
```

Reuse the same `VAPI_WEBHOOK_SECRET` value as the old project — it's just a
shared-secret string, not tied to infra, and the Vapi assistant already has
it configured as the header value.

Setting `DATABASE_URL` triggers a redeploy automatically (unless
`skip_deploys: true` is passed).

### 6. Generate a public domain

```
mcp__railway__generate_domain
  project_id=<project_id>
  service_id=<app_service_id>
  environment_id=<environment_id>
```

Returns the new `https://<name>.up.railway.app` URL.

### 7. Point Vapi at the new URL — one script, not the dashboard

**Gotcha that bit us once:** Vapi stores the webhook URL in *four* places —
the assistant's `server.url`, and separately on each of the three custom
tools (`lookup_patient`, `register_patient`, `update_patient`). The
assistant-level URL is **not** a fallback for tool calls. Patching only the
assistant leaves the tools pointed at the dead old domain: every call
*looks* fine (Vapi's webhook health checks and non-tool messages still hit
the assistant URL and return 200) but every `lookup_patient` /
`register_patient` / `update_patient` invocation fails mid-call with Vapi's
generic "No result returned" error — silently, with no server-side log line,
because the request never reaches this app at all.

Fix: update `serverUrl` in `vapi/assistant.json`, then run one script that
patches the assistant AND all three tools from that single value, and
verifies afterward by re-fetching everything and diffing:

```powershell
./vapi/apply-assistant.ps1
```

Reads `VAPI_PRIVATE_KEY`, `VAPI_ASSISTANT_ID`, `VAPI_WEBHOOK_SECRET` from the
environment (see the script's header comment for where to find each in the
Vapi dashboard). It throws if any tool or the assistant still shows a
mismatched URL after the patch — don't consider the migration done until it
prints "All server URLs match".

### Notes

- The old project's `DATABASE_URL` is internal-only (`postgres.railway.internal`) —
  not reusable across projects/accounts even if the old trial is still technically
  alive. Data migration would need a TCP proxy + `pg_dump` from the old Postgres
  service before the account is gone for good.
- Free/trial Railway accounts may flag duplicate signups (device, card, email
  fingerprint) — a second trial isn't guaranteed to work indefinitely.

---

## Vapi account migration (also CLI/API, no dashboard)

Needed when the Vapi org itself runs out of credits (`Wallet Balance is
-0.05` in the dashboard) — a new Railway backend does nothing for that; Vapi
credits are a separate balance on a separate account. Signing up fresh on
another email gets a new org with its own trial credits, but that org starts
**empty**: no assistant, no tools, no phone number. Everything has to be
recreated, not just repointed.

### 1. Get the new account's private key

Vapi dashboard (new account) → Organization Settings → API Keys → private
key. Set it locally:

```powershell
[Environment]::SetEnvironmentVariable("VAPI_PRIVATE_KEY", "<new key>", "User")
```

Open a fresh shell after setting it (env vars only propagate to new
processes). Sanity check you're on the new org, not the old one, before
creating anything:

```powershell
$key = [Environment]::GetEnvironmentVariable('VAPI_PRIVATE_KEY','User')
Invoke-RestMethod -Uri "https://api.vapi.ai/assistant" -Headers @{ Authorization = "Bearer $key" } |
    Select-Object id, name, orgId
```

A brand new org shows Vapi's default demo assistant (e.g. named "Riley"),
never "Ava" — if "Ava" already shows up, the key is still pointed at the old
account.

### 2. Create the tools + assistant from scratch

`vapi/assistant.json` already carries the full tool schemas (function
definitions for `lookup_patient`, `register_patient`, `update_patient`, plus
`end_call`), so nothing needs retyping:

```powershell
./vapi/create-assistant.ps1
```

Reads `VAPI_PRIVATE_KEY` and `VAPI_WEBHOOK_SECRET` from the environment.
`VAPI_WEBHOOK_SECRET` can be the same value as before, or a new one — just
make sure the value the app checks (Railway's `VAPI_WEBHOOK_SECRET` var) and
the value Vapi sends match. Prints the new `VAPI_ASSISTANT_ID` at the end —
save it the same way (`SetEnvironmentVariable`, new shell).

This is a one-time bootstrap per Vapi org. Every config change after this
(prompt edits, voice tuning, webhook URL) goes through
`./vapi/apply-assistant.ps1`, which PATCHes what this script created and
verifies every tool + the assistant agree on the same server URL — the check
that would have caught the "tools still point at the dead domain" bug from
the Railway migration above.

### 3. Provision a free US number

Vapi hosts free numbers directly (no Twilio account needed):

```powershell
$key = [Environment]::GetEnvironmentVariable('VAPI_PRIVATE_KEY','User')
$id  = [Environment]::GetEnvironmentVariable('VAPI_ASSISTANT_ID','User')
$headers = @{ Authorization = "Bearer $key" }
$payload = @{ provider = "vapi"; assistantId = $id; name = "Ava Intake Line"; numberDesiredAreaCode = "502" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "https://api.vapi.ai/phone-number" -Headers $headers -ContentType "application/json" -Body $payload
```

Not every area code is available — a 400 response names three that are
(`Hint: Try one of X, Y, Z`); retry with one of those. Status starts as
`"activating"` and goes live within a minute or two.

### 4. Update every place the old number/URL is documented

Both change per migration — old domain (Railway) and old phone number
(Vapi), independently:

- `README.md` — phone number table, `/docs` and `/dashboard` links, the
  `BASE=` line in the curl examples.
- `docs/ARCHITECTURE.md` — the phone number in the telephony diagram node.

### 5. Verify end-to-end before calling it done

Don't trust a clean-sounding transcript alone — Vapi's webhook errors
("No result returned") don't always show up as spoken failure; check the
actual tool-call results:

```powershell
$key = [Environment]::GetEnvironmentVariable('VAPI_PRIVATE_KEY','User')
$headers = @{ Authorization = "Bearer $key" }
$c = Invoke-RestMethod -Uri "https://api.vapi.ai/call?limit=1" -Headers $headers
$c.messages | Where-Object { $_.role -eq 'tool_call_result' } | Select-Object name, result
```

`register_patient` should show `result: "success|<patient_id>|<first_name>"`.
Then confirm the record actually landed in Postgres via the app's own API:

```powershell
curl -s "https://<app-domain>/patients/<patient_id>"
```
