# Pushes the tuned settings in assistant.json to a live Vapi assistant.
#
# The dashboard holds tools as separate published entities, so this sends
# toolIds fetched from the API rather than the inline definitions in
# assistant.json -- sending those would duplicate them. PATCHing "model"
# replaces the whole object, so toolIds MUST be included or the assistant
# loses every tool.
#
# The system prompt comes from prompts/system_prompt.md (the code fence), not
# from assistant.json, whose systemPrompt field is a placeholder.
#
# Reads three variables from the environment (process, user, or machine scope):
#   VAPI_PRIVATE_KEY      Vapi -> Organization Settings -> API Keys (private)
#   VAPI_ASSISTANT_ID     Assistants -> Ava -> id shown under the name
#   VAPI_WEBHOOK_SECRET   must match Railway's VAPI_WEBHOOK_SECRET
#
# Set them once at user scope so they survive new shells:
#   [Environment]::SetEnvironmentVariable("VAPI_PRIVATE_KEY", "...", "User")
#
# Usage:
#   ./vapi/apply-assistant.ps1

$ErrorActionPreference = "Stop"

# Read from the process env, falling back to the persisted User scope so a
# shell opened before the variables were set still works. Values are never
# printed.
function Get-RequiredVar([string]$name, [string]$hint) {
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    if (-not $value) { $value = [Environment]::GetEnvironmentVariable($name, "User") }
    if (-not $value) { $value = [Environment]::GetEnvironmentVariable($name, "Machine") }
    if (-not $value) { throw "$name is not set. $hint" }
    return $value
}

$privateKey    = Get-RequiredVar "VAPI_PRIVATE_KEY"    "Vapi dashboard -> Organization Settings -> API Keys (private key)."
$assistantId   = Get-RequiredVar "VAPI_ASSISTANT_ID"   "Assistants -> Ava -> the id shown under the name."
$webhookSecret = Get-RequiredVar "VAPI_WEBHOOK_SECRET" "Must match Railway's VAPI_WEBHOOK_SECRET."

$root = Split-Path $PSScriptRoot -Parent
$cfg = Get-Content "$root/vapi/assistant.json" -Raw | ConvertFrom-Json

# The prompt lives inside the first ``` fence of the markdown file.
$md = Get-Content "$root/prompts/system_prompt.md" -Raw
if ($md -notmatch '(?s)```\r?\n(.*?)```') { throw "No code fence found in system_prompt.md" }
$systemPrompt = $Matches[1].Trim()

$headers = @{ Authorization = "Bearer $privateKey" }

# Attach every tool in the account. Fine while this org has exactly the four
# tools this assistant needs; filter by name here if that stops being true.
$tools = Invoke-RestMethod -Uri "https://api.vapi.ai/tool" -Headers $headers
$toolIds = @($tools | ForEach-Object { $_.id })
Write-Host "Attaching $($toolIds.Count) tools:" ($tools | ForEach-Object { $_.function.name ?? $_.type })

$payload = @{
    # Assistant-level server: where end-of-call-report lands. The per-tool
    # server URLs only receive tool-calls, so without this transcripts are
    # never delivered. Secret comes from the environment, never the repo.
    server                 = @{
        url    = $cfg.serverUrl
        secret = $webhookSecret
    }
    firstMessage           = $cfg.firstMessage
    voice                  = $cfg.voice
    transcriber            = $cfg.transcriber
    startSpeakingPlan      = $cfg.startSpeakingPlan
    stopSpeakingPlan       = $cfg.stopSpeakingPlan
    silenceTimeoutSeconds  = $cfg.silenceTimeoutSeconds
    messagePlan            = $cfg.messagePlan
    model                  = @{
        provider    = $cfg.model.provider
        model       = $cfg.model.model
        temperature = $cfg.model.temperature
        messages    = @(@{ role = "system"; content = $systemPrompt })
        toolIds     = $toolIds
    }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Patch `
    -Uri "https://api.vapi.ai/assistant/$assistantId" `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $payload | Select-Object id, name, updatedAt
