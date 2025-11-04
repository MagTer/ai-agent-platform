<# Brings the stack down from anywhere (double-click friendly).
   Keeps volumes by default. Use -RemoveVolumes to purge data. ASCII-only. #>

[CmdletBinding()]
param(
  [switch]$RemoveVolumes,
  [switch]$KeepVolumes
)

$ErrorActionPreference = "Stop"

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

$composeArgs = @('-f', $composeFile)
if ($composeProjectName) {
  $composeArgs += @('-p', $composeProjectName)
}

# Ensure compose reads the repo .env explicitly to avoid env resolution issues
$envFile = Join-Path $repoRoot '.env'
if (Test-Path $envFile) {
  $composeArgs += @('--env-file', $envFile)
}

if ($RemoveVolumes -and $KeepVolumes) {
  Write-Error "Cannot specify both -RemoveVolumes and -KeepVolumes."; exit 1
}

$removeVolumes = $false
if ($RemoveVolumes) {
  $removeVolumes = $true
} elseif ($KeepVolumes) {
  $removeVolumes = $false
}

Push-Location $repoRoot
try {
  if ($composeProjectName) { Say "[i] Using compose project: $composeProjectName" "Yellow" }

  if ($removeVolumes) {
    Say "[i] Bringing stack down and removing volumes..." "Yellow"
    docker compose @composeArgs down -v --remove-orphans
  } else {
    Say "[i] Bringing stack down (keeping volumes)..." "Yellow"
    docker compose @composeArgs down --remove-orphans
  }
  Say "[OK] Stack is down." "Green"
}
finally {
  Pop-Location
}
