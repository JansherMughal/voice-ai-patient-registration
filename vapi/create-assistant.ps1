# One-time bootstrap for a brand new Vapi account/org: creates the 4 tools
# and the assistant from scratch, then prints the VAPI_ASSISTANT_ID to save.
#
# Run this ONCE per new Vapi account. After it succeeds, use
# ./vapi/apply-assistant.ps1 for every future config change (voice, prompt,
# server URL, etc) -- that script PATCHes what this one creates.
#
# Reads from the environment (process, user, or machine scope):
#   VAPI_PRIVATE_KEY      New account -> Organization Settings -> API Keys (private)
#   VAPI_WEBHOOK_SECRET   Any shared-secret string; must match the app's
#                          VAPI_WEBHOOK_SECRET env var on Railway
#
# Usage:
#   ./vapi/create-assistant.ps1
#   [Environment]::SetEnvironmentVariable("VAPI_ASSISTANT_ID", "<id printed above>", "User")

$ErrorActionPreference = "Stop"

function Get-RequiredVar([string]$name, [string]$hint) {
    $value = [Environment]::GetEnvironmentVariable($name, "Process")
    if (-not $value) { $value = [Environment]::GetEnvironmentVariable($name, "User") }
    if (-not $value) { $value = [Environment]::GetEnvironmentVariable($name, "Machine") }
    if (-not $value) { throw "$name is not set. $hint" }
    return $value
}

$privateKey    = Get-RequiredVar "VAPI_PRIVATE_KEY"    "New Vapi account -> Organization Settings -> API Keys (private key)."
$webhookSecret = Get-RequiredVar "VAPI_WEBHOOK_SECRET" "Any shared-secret string; must match the app's VAPI_WEBHOOK_SECRET on Railway."

$root = Split-Path $PSScriptRoot -Parent
$cfg = Get-Content "$root/vapi/assistant.json" -Raw | ConvertFrom-Json

$md = Get-Content "$root/prompts/system_prompt.md" -Raw
if ($md -notmatch '(?s)```\r?\n(.*?)```') { throw "No code fence found in system_prompt.md" }
$systemPrompt = $Matches[1].Trim()

$headers = @{ Authorization = "Bearer $privateKey" }
$serverConfig = @{
    url            = $cfg.serverUrl
    timeoutSeconds = 20
    headers        = @{ "x-vapi-secret" = $webhookSecret }
}

Write-Host "Creating tools..."
$toolIds = @()
foreach ($toolDef in $cfg.model.tools) {
    $payload = @{ type = $toolDef.type; function = $toolDef.function }
    if ($toolDef.type -ne "endCall") { $payload.server = $serverConfig }
    if ($toolDef.messages) { $payload.messages = $toolDef.messages }
    $body = $payload | ConvertTo-Json -Depth 10
    $created = Invoke-RestMethod -Method Post -Uri "https://api.vapi.ai/tool" `
        -Headers $headers -ContentType "application/json" -Body $body
    Write-Host "  $($toolDef.function.name): $($created.id)"
    $toolIds += $created.id
}

Write-Host "Creating assistant..."
$payload = @{
    name                   = $cfg.name
    firstMessage           = $cfg.firstMessage
    voice                  = $cfg.voice
    transcriber            = $cfg.transcriber
    startSpeakingPlan      = $cfg.startSpeakingPlan
    stopSpeakingPlan       = $cfg.stopSpeakingPlan
    silenceTimeoutSeconds  = $cfg.silenceTimeoutSeconds
    messagePlan            = $cfg.messagePlan
    backgroundDenoisingEnabled = $cfg.backgroundDenoisingEnabled
    server                 = $serverConfig
    model                  = @{
        provider    = $cfg.model.provider
        model       = $cfg.model.model
        temperature = $cfg.model.temperature
        messages    = @(@{ role = "system"; content = $systemPrompt })
        toolIds     = $toolIds
    }
} | ConvertTo-Json -Depth 10

$assistant = Invoke-RestMethod -Method Post -Uri "https://api.vapi.ai/assistant" `
    -Headers $headers -ContentType "application/json" -Body $payload

Write-Host "`nAssistant created: $($assistant.name) ($($assistant.id))"
Write-Host "Save it: [Environment]::SetEnvironmentVariable(`"VAPI_ASSISTANT_ID`", `"$($assistant.id)`", `"User`")"
