[CmdletBinding()]
param(
  [string]$BackupDir = 'backups',
  [string]$Container = 'qdrant',
  [string]$ComposeFile = 'docker-compose.yml'
)

$ErrorActionPreference = 'Stop'

function Resolve-RepoRoot {
    param([string]$Start)
    $dir = Resolve-Path $Start
    for ($i=0; $i -lt 10; $i++) {
      $compose = Join-Path $dir "docker-compose.yml"
      if (Test-Path $compose) { return (Split-Path $compose -Parent) }
      $parent = Split-Path $dir -Parent
      if ($parent -eq $dir) { break }
      $dir = $parent
    }
    throw "Could not find docker-compose.yml upwards from $Start"
  }

function Exec {
  param([Parameter(Mandatory)][string]$File,
        [Parameter()][string[]]$Args=@())
  $out = & $File @Args 2>&1
  $code = $LASTEXITCODE
  if ($code -ne 0) { throw "Command failed: $File $($Args -join ' ') (exit $code)\n$out" }
  return $out
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-RepoRoot -Start $scriptRoot

Write-Host "[i] Repo root: $repoRoot" -ForegroundColor Yellow
Write-Host "[i] Container: $Container" -ForegroundColor Yellow

Push-Location $repoRoot
try {
  # Ensure container exists
  Exec docker @('inspect',$Container,'--format','{{.Name}}') | Out-Null

  $destDir = Join-Path $repoRoot $BackupDir
  if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }

  $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
  $archive = Join-Path $destDir "qdrant-$ts.tgz"

  Write-Host "[i] Creating Qdrant storage backup -> $archive" -ForegroundColor Cyan
  # Use a throwaway Alpine container with volumes-from to read the storage from the running container
  Exec docker @('run','--rm','--volumes-from', $Container, '-v', "$destDir`:/backup", 'alpine', 'sh','-lc', "tar czf /backup/$(basename $archive) /qdrant/storage") | Out-Null
  Write-Host "[ok] Backup created: $archive" -ForegroundColor Green
}
finally { Pop-Location }

