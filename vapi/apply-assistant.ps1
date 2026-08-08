# Pushes the tuned settings in assistant.json to a live Vapi assistant.
#
# Deliberately does NOT send "tools" -- the dashboard holds those as separate
# published entities, and sending the inline copies here would duplicate them.
# The system prompt comes from prompts/system_prompt.md (the code fence), not
# from assistant.json, whose systemPrompt field is a placeholder.
#
# Usage:
#   $env:VAPI_PRIVATE_KEY = "..."      # Vapi dashboard -> Settings -> API Keys
#   $env:VAPI_ASSISTANT_ID = "..."     # Assistants -> Ava -> id under the name
#   ./vapi/apply-assistant.ps1

$ErrorActionPreference = "Stop"

if (-not $env:VAPI_PRIVATE_KEY)   { throw "Set VAPI_PRIVATE_KEY first" }
if (-not $env:VAPI_ASSISTANT_ID)  { throw "Set VAPI_ASSISTANT_ID first" }

$root = Split-Path $PSScriptRoot -Parent
$cfg = Get-Content "$root/vapi/assistant.json" -Raw | ConvertFrom-Json

# The prompt lives inside the first ``` fence of the markdown file.
$md = Get-Content "$root/prompts/system_prompt.md" -Raw
if ($md -notmatch '(?s)```\r?\n(.*?)```') { throw "No code fence found in system_prompt.md" }
$systemPrompt = $Matches[1].Trim()

$payload = @{
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
    }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Patch `
    -Uri "https://api.vapi.ai/assistant/$env:VAPI_ASSISTANT_ID" `
    -Headers @{ Authorization = "Bearer $env:VAPI_PRIVATE_KEY" } `
    -ContentType "application/json" `
    -Body $payload | Select-Object id, name, updatedAt
