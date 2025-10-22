<# Tails container logs. Double-click friendly. ASCII-only.

Usage:
  - Double-click: tails all core services (ollama, litellm, openwebui, qdrant)
  - Or run with:  pwsh .\scripts\Stack-Logs.ps1 -Service litellm
#>

[CmdletBinding()]
param(
  [string]$Service = ""
)

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

$targets = @("ollama","litellm","openwebui","qdrant")
if ($Service -ne "") { $targets = @($Service) }

Push-Location $repoRoot
try {
  foreach ($t in $targets) {
    Say "[i] Tailing logs for: $t" "Yellow"
    # Use --since to avoid huge scroll on first run; remove if you want full history
    try {
      docker logs -f --since 5m $t
    } catch {
      Say "[!] Failed to read logs for $t. Is the container running?" "Red"
    }
  }
}
finally { Pop-Location }
