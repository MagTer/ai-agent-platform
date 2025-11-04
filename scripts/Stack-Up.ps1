<# Starts the stack (double-click friendly), waits for Ollama, pulls models if missing,
   and optionally waits for LiteLLM health. ASCII-only; works from any folder. #>

[CmdletBinding()]
param(
  [switch]$CheckLiteLLM = $false,
  [switch]$Build,
  [switch]$BindMounts
)

$ErrorActionPreference = "Stop"

# ---- Configuration ----
# Default models; can be overridden by config/models.txt in repo root
$Models = @(
  "qwen2.5:14b-instruct-q4_K_M"
)

$OllamaHealthTimeoutSec  = 120
$LiteLLMHealthTimeoutSec = 120

function Find-ComposePath {
  param([string]$StartDir)
  $dir = Resolve-Path $StartDir
  for ($i = 0; $i -lt 10; $i++) {
    $candidate = Join-Path $dir "docker-compose.yml"
    if (Test-Path $candidate) { return $candidate }
    $parent = Split-Path $dir -Parent
    if ($parent -eq $dir) { break }
    $dir = $parent
  }
  return $null
}

function Say($msg, $color="Cyan") { Write-Host $msg -ForegroundColor $color }

function Get-MappedPort {
  param(
    [Parameter(Mandatory)] [string]$ContainerName,
    [Parameter(Mandatory)] [int]$InternalPort
  )
  try {
    $map = docker port $ContainerName "$InternalPort/tcp" 2>$null
    if ($map) {
      $p = ($map -split ":" | Select-Object -Last 1).Trim()
      if ($p -match '^\d+$') { return [int]$p }
    }
  } catch {}
  return $InternalPort
}

function Wait-HttpOk {
  param([string]$Url, [int]$TimeoutSec)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
      if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) { return $true }
    } catch {}
    Start-Sleep -Seconds 2
  }
  return $false
}

function Ensure-Models {
  param([string[]]$ModelNames)
  if (-not $ModelNames -or $ModelNames.Count -eq 0) { return }

  Say "[i] Ensuring models: $($ModelNames -join ', ')"
  foreach ($m in $ModelNames) {
    Say "[i] Ensuring model: $m"
    # Run shell inside container; if model is missing -> pull it.
    $cmd = "if ! ollama list | grep -q `"$m`"; then ollama pull `"$m`"; fi"
    docker exec ollama /bin/sh -lc "$cmd"
  }
}


# ---- Preconditions ----
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Error "Docker is not available in PATH."; exit 1
}

$composeFile = Find-ComposePath -StartDir $PSScriptRoot
if (-not $composeFile) { Write-Error "Could not find docker-compose.yml upward from $PSScriptRoot"; exit 1 }
$repoRoot = Split-Path $composeFile -Parent

function Get-EnvValue {
  param(
    [Parameter(Mandatory)][string]$FilePath,
    [Parameter(Mandatory)][string]$Key
  )

  if (-not (Test-Path $FilePath)) { return $null }

  foreach ($line in Get-Content $FilePath) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }

    $idx = $trimmed.IndexOf('=')
    if ($idx -lt 0) { continue }

    $name  = $trimmed.Substring(0, $idx).Trim()
    $value = $trimmed.Substring($idx + 1).Trim()
    if ($name -ne $Key) { continue }

    if ($value.StartsWith('"') -and $value.EndsWith('"')) { return $value.Trim('"') }
    if ($value.StartsWith("'") -and $value.EndsWith("'")) { return $value.Trim("'") }
    return $value
  }

  return $null
}

$composeDir = Split-Path $composeFile -Parent
$composeProjectName = Get-EnvValue -FilePath (Join-Path $repoRoot '.env') -Key 'COMPOSE_PROJECT_NAME'
if (-not $composeProjectName) {
  $composeProjectName = Get-EnvValue -FilePath (Join-Path $repoRoot '.env.template') -Key 'COMPOSE_PROJECT_NAME'
}

# Override models from config/models.txt if present
$modelsFile = Join-Path $repoRoot 'config\models.txt'
if (Test-Path $modelsFile) {
  try {
    $lines = Get-Content $modelsFile | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') }
    if ($lines.Count -gt 0) { $Models = $lines }
  } catch {
    Say "[!] Failed to read config/models.txt; using defaults" "Yellow"
  }
}

$composeArgs = @('-f', $composeFile)
if ($composeProjectName) {
  $composeArgs += @('-p', $composeProjectName)
}

# Optional override for bind mounts
if ($BindMounts) {
  $bindOverride = Join-Path $repoRoot 'compose\docker-compose.bind.yml'
  if (-not (Test-Path $bindOverride)) {
    Write-Error "Bind override not found: $bindOverride"; exit 1
  }
  $composeArgs += @('-f', $bindOverride)
}

# Ensure compose reads the repo .env explicitly to avoid env resolution issues
$envFile = Join-Path $repoRoot '.env'
if (Test-Path $envFile) {
  $composeArgs += @('--env-file', $envFile)
}

# Validate required secrets for Open WebUI and SearxNG
$owSecret = Get-EnvValue -FilePath (Join-Path $repoRoot '.env') -Key 'OPENWEBUI_SECRET'
if (-not $owSecret) { $owSecret = Get-EnvValue -FilePath (Join-Path $repoRoot '.env.template') -Key 'OPENWEBUI_SECRET' }
$sxSecret = Get-EnvValue -FilePath (Join-Path $repoRoot '.env') -Key 'SEARXNG_SECRET'
if (-not $sxSecret) { $sxSecret = Get-EnvValue -FilePath (Join-Path $repoRoot '.env.template') -Key 'SEARXNG_SECRET' }
if (-not $owSecret -or [string]::IsNullOrWhiteSpace($owSecret)) {
  Write-Error "OPENWEBUI_SECRET is required. Set it in .env."; exit 1
}
if (-not $sxSecret -or [string]::IsNullOrWhiteSpace($sxSecret)) {
  Write-Error "SEARXNG_SECRET is required. Set it in .env."; exit 1
}

Push-Location $repoRoot
try {
  if (-not (Test-Path $composeFile)) { Write-Error "docker-compose.yml is missing."; exit 1 }

  Say "[i] Starting stack using: $composeFile" "Yellow"
  if ($composeProjectName) { Say "[i] Using compose project: $composeProjectName" "Yellow" }
  $upArgs = @('up','-d')
  if ($Build) { $upArgs += '--build' }
  docker compose @composeArgs @upArgs

  # Wait for Ollama health
  $ollamaPort = Get-MappedPort -ContainerName "ollama" -InternalPort 11434
  Say "[i] Waiting for Ollama on port $ollamaPort ..."
  if (-not (Wait-HttpOk -Url "http://localhost:$ollamaPort/api/version" -TimeoutSec $OllamaHealthTimeoutSec)) {
    Write-Error "Ollama did not become healthy within timeout. See: docker logs ollama"; exit 1
  }

  # Ensure models exist inside container
  Ensure-Models -ModelNames $Models

  # Optional: wait for LiteLLM health
  if ($CheckLiteLLM) {
    $litellmPort = Get-MappedPort -ContainerName "litellm" -InternalPort 4000
    Say "[i] Waiting for LiteLLM on port $litellmPort ..."
    if (-not (Wait-HttpOk -Url "http://localhost:$litellmPort/health" -TimeoutSec $LiteLLMHealthTimeoutSec)) {
      Say "[!] LiteLLM health endpoint not responding in time. Check logs: docker logs litellm" "Yellow"
    }
  }

  # Additional health checks (fail fast, lightweight)
  $sxPort = Get-MappedPort -ContainerName "searxng" -InternalPort 8080
  Say "[i] Waiting for SearxNG on port $sxPort ..."
  if (-not (Wait-HttpOk -Url "http://localhost:$sxPort/" -TimeoutSec 60)) { Write-Error "SearxNG not healthy."; exit 1 }

  $wfPort = Get-MappedPort -ContainerName "webfetch" -InternalPort 8081
  Say "[i] Waiting for webfetch on port $wfPort ..."
  if (-not (Wait-HttpOk -Url "http://localhost:$wfPort/health" -TimeoutSec 60)) { Write-Error "webfetch not healthy."; exit 1 }

  $qdPort = Get-MappedPort -ContainerName "qdrant" -InternalPort 6333
  Say "[i] Waiting for Qdrant on port $qdPort ..."
  if (-not (Wait-HttpOk -Url "http://localhost:$qdPort/healthz" -TimeoutSec 60)) { Write-Error "Qdrant not healthy."; exit 1 }

  $embPort = Get-MappedPort -ContainerName "embedder" -InternalPort 8082
  Say "[i] Waiting for embedder on port $embPort ..."
  if (-not (Wait-HttpOk -Url "http://localhost:$embPort/health" -TimeoutSec 60)) { Write-Error "Embedder not healthy."; exit 1 }

  $n8nPort = Get-MappedPort -ContainerName "n8n" -InternalPort 5678
  Say "[i] Waiting for n8n on port $n8nPort ..."
  if (-not (Wait-HttpOk -Url "http://localhost:$n8nPort/healthz" -TimeoutSec 60)) { Write-Error "n8n not healthy."; exit 1 }

  # Show status
  Say "[i] Containers status:" "Cyan"
  docker compose @composeArgs ps

  Say "[OK] Stack is up and models are ensured." "Green"
}
finally {
  Pop-Location
}
