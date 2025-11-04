[CmdletBinding()]
param(
  [Parameter(Mandatory)] [string]$BackupFile,
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

Push-Location $repoRoot
try {
  $backupPath = Resolve-Path $BackupFile
  if (-not (Test-Path $backupPath)) { throw "Backup file not found: $BackupFile" }

  Write-Host "[i] Stopping Qdrant via compose" -ForegroundColor Yellow
  Exec docker @('compose','-f',(Resolve-Path $ComposeFile).Path,'stop','qdrant') | Out-Null

  Write-Host "[i] Restoring Qdrant storage from $backupPath" -ForegroundColor Cyan
  $backupDir = Split-Path -Parent $backupPath
  $backupBase = Split-Path -Leaf $backupPath
  Exec docker @('run','--rm','--volumes-from', $Container, '-v', "$backupDir`:/backup", 'alpine', 'sh','-lc', "rm -rf /qdrant/storage/* && tar xzf /backup/$backupBase -C /") | Out-Null

  Write-Host "[i] Starting Qdrant via compose" -ForegroundColor Yellow
  Exec docker @('compose','-f',(Resolve-Path $ComposeFile).Path,'start','qdrant') | Out-Null
  Write-Host "[ok] Restore completed." -ForegroundColor Green
}
finally { Pop-Location }

