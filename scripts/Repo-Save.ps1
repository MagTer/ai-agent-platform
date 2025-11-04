<# Saves the repo (git add/commit) from anywhere. ASCII-only, double-click friendly.
   - Validates docker compose config (if present).
   - Creates a timestamped commit only when there are changes.
#>

[CmdletBinding()]
param(
  [string]$Message = "chore: save workspace"
)

$ErrorActionPreference = "Stop"

function Find-ComposePath {
  param([string]$StartDir)
  $dir = Resolve-Path $StartDir
  for ($i=0; $i -lt 10; $i++) {
    $candidate = Join-Path $dir "docker-compose.yml"
    if (Test-Path $candidate) { return $candidate }
    $parent = Split-Path $dir -Parent
    if ($parent -eq $dir) { break }
    $dir = $parent
  }
  return $null
}
function Say($m,$c="Cyan"){ Write-Host $m -ForegroundColor $c }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Error "git is not available in PATH."; exit 1
}

$composeFile = Find-ComposePath -StartDir $PSScriptRoot
if (-not $composeFile) {
  # Fallback: assume scripts\ is directly under repo root
  $repoRoot = Split-Path $PSScriptRoot -Parent
} else {
  $repoRoot = Split-Path $composeFile -Parent
}

Push-Location $repoRoot
try {
  if (-not (Test-Path ".git")) {
    Say "[i] No git repo detected, initializing..." "Yellow"
    git -c init.defaultBranch=main init | Out-Null
  }

  # Optional: validate docker compose config if present
  if (Test-Path "docker-compose.yml") {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
      Say "[i] Validating docker compose config..." "Yellow"
      $envFile = Join-Path $repoRoot '.env'
      $args = @('compose','-f','docker-compose.yml')
      if (Test-Path $envFile) { $args += @('--env-file', $envFile) }
      $args += 'config'
      docker @args | Out-Null
    } else {
      Say "[i] Docker not available; skipping compose validation." "Yellow"
    }
  }

  # Stage and commit if there are changes
  git add -A | Out-Null
  $changes = git status --porcelain
  if ($changes) {
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $finalMsg = "$Message ($ts)"
    git commit -m "$finalMsg" | Out-Null
    Say "[OK] Saved changes: $finalMsg" "Green"
  } else {
    Say "[i] No changes to commit." "Cyan"
  }
}
finally {
  Pop-Location
}
