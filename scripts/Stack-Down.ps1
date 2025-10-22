<# Brings the stack down from anywhere (double-click friendly).
   By default removes volumes. Use -KeepVolumes to preserve data. ASCII-only. #>

[CmdletBinding()]
param([switch]$KeepVolumes)

$ErrorActionPreference = "Stop"

function Find-ComposePath {
  param([string]$StartDir)
  $dir = Resolve-Path $StartDir
  for ($i = 0; $i -lt 10; $i++) {
    $candidate = Join-Path $dir "compose\docker-compose.yml"
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
if (-not $composeFile) { Write-Error "Could not find compose\docker-compose.yml upward from $PSScriptRoot"; exit 1 }
$repoRoot = Split-Path (Split-Path $composeFile -Parent) -Parent

Push-Location $repoRoot
try {
  if ($true) {
    Say "[i] Bringing stack down (keeping volumes)..." "Yellow"
    docker compose -f $composeFile down --remove-orphans
  } else {
    Say "[i] Bringing stack down and removing volumes..." "Yellow"
    docker compose -f $composeFile down -v --remove-orphans
  }
  Say "[OK] Stack is down." "Green"
}
finally {
  Pop-Location
}
