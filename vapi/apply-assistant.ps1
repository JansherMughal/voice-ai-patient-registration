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
# Reads two variables from the environment (process, user, or machine scope):
#   VAPI_PRIVATE_KEY      Vapi -> Organization Settings -> API Keys (private)
#   VAPI_ASSISTANT_ID     Assistants -> Ava -> id shown under the name
#
# Also pushes the webhook URL + secret to every place Vapi stores them
# separately: the assistant's own `server` field AND each tool's own
# `server` field (tools have their own webhook target independent of the
# assistant -- the assistant-level URL is NOT a fallback for tool calls).
# Missing the tool-level URLs after a redeploy/migration means every
# lookup_patient/register_patient/update_patient call silently fails with
# Vapi's "No result returned" error while the assistant itself looks fine.
#
# Patching assistant.server together with its legacy serverUrl field in the
# same request returned a 500 -- only serverUrl is sent, never both.
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

$privateKey     = Get-RequiredVar "VAPI_PRIVATE_KEY"     "Vapi dashboard -> Organization Settings -> API Keys (private key)."
$assistantId    = Get-RequiredVar "VAPI_ASSISTANT_ID"    "Assistants -> Ava -> the id shown under the name."
$webhookSecret  = Get-RequiredVar "VAPI_WEBHOOK_SECRET"  "Same value set as the app's VAPI_WEBHOOK_SECRET env var on Railway."

$root = Split-Path $PSScriptRoot -Parent
$cfg = Get-Content "$root/vapi/assistant.json" -Raw | ConvertFrom-Json

# assistant.json's serverUrl is the single source of truth for the webhook
# target -- update it there and every place below follows.
$serverConfig = @{
    url            = $cfg.serverUrl
    timeoutSeconds = 20
    headers        = @{ "x-vapi-secret" = $webhookSecret }
}

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

# Each tool carries its own webhook target, independent of the assistant's --
# push serverConfig to every tool that has one (end_call has no server).
foreach ($tool in $tools) {
    if (-not $tool.server) { continue }
    $toolPayload = @{ server = $serverConfig } | ConvertTo-Json -Depth 5
    Invoke-RestMethod -Method Patch -Uri "https://api.vapi.ai/tool/$($tool.id)" `
        -Headers $headers -ContentType "application/json" -Body $toolPayload | Out-Null
}

$payload = @{
    firstMessage           = $cfg.firstMessage
    voice                  = $cfg.voice
    transcriber            = $cfg.transcriber
    startSpeakingPlan      = $cfg.startSpeakingPlan
    stopSpeakingPlan       = $cfg.stopSpeakingPlan
    silenceTimeoutSeconds  = $cfg.silenceTimeoutSeconds
    messagePlan            = $cfg.messagePlan
    server                 = $serverConfig
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

# Verify: re-fetch and confirm every server.url actually landed on the
# target domain. This is the check that would have caught the migration bug
# -- all POSTs return 200 even when a tool's server.url is stale, because
# the failure only shows up as Vapi's "No result returned" mid-call.
Write-Host "`nVerifying server URLs..."
$freshTools = Invoke-RestMethod -Uri "https://api.vapi.ai/tool" -Headers $headers
$freshAssistant = Invoke-RestMethod -Uri "https://api.vapi.ai/assistant/$assistantId" -Headers $headers
$mismatches = @()
if ($freshAssistant.server.url -ne $cfg.serverUrl) {
    $mismatches += "assistant Ava: $($freshAssistant.server.url)"
}
foreach ($tool in $freshTools) {
    if ($tool.server -and $tool.server.url -ne $cfg.serverUrl) {
        $mismatches += "tool $($tool.function.name): $($tool.server.url)"
    }
}
if ($mismatches.Count -gt 0) {
    throw "Server URL mismatch after apply, expected $($cfg.serverUrl):`n$($mismatches -join "`n")"
}
Write-Host "All server URLs match $($cfg.serverUrl)"
