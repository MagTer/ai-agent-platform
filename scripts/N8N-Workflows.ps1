<# 
.N8N-Workflows.ps1
Import/export n8n workflows between the running container and the repository.

Usage:
  .\N8N-Workflows.ps1 import        # repo -> container
  .\N8N-Workflows.ps1 export        # container -> repo
  .\N8N-Workflows.ps1 -Mode import -Container n8n -FlowsDir "..\flows"

Notes:
- ASCII only. Works from any folder (uses Push-Location/Pop-Location).
- Uses n8n CLI inside the container:
    n8n import:workflow --separate --input <dir>
    n8n export:workflow --all --pretty --separate --output <dir>
- Requires the container name (default: 'n8n').
- Flows are stored in repo folder 'flows' by default.

#>

[CmdletBinding()]
param(
  [ValidateSet("import","export")]
  [string]$Mode = "import",
  [string]$Container = "n8n",
  [string]$FlowsDir = "..\flows"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
  param([string]$Start)
  $dir = Resolve-Path $Start
  for ($i=0; $i -lt 10; $i++) {
    $compose = Join-Path $dir "compose\docker-compose.yml"
    if (Test-Path $compose) { return Split-Path $compose -Parent | Split-Path -Parent }
    $parent = Split-Path $dir -Parent
    if ($parent -eq $dir) { break }
    $dir = $parent
  }
  throw "Could not find compose\docker-compose.yml upwards from $Start"
}

function Join-ContainerPath {
  param(
    [Parameter(Mandatory)][string]$Container,
    [Parameter(Mandatory)][string]$Path
  )
  if (-not $Path.StartsWith("/")) { $Path = "/$Path" }
  return "${Container}:$Path"
}

function Exec {
  param(
    [Parameter(Mandatory)][string]$File,
    [Parameter()][string[]]$Args = @()
  )
  Write-Host ">> $File $($Args -join ' ')" -ForegroundColor DarkGray
  $p = Start-Process -FilePath $File -ArgumentList $Args -NoNewWindow -Wait -PassThru
  if ($p.ExitCode -ne 0) {
    throw "Command failed: $File $($Args -join ' ') (exit $($p.ExitCode))"
  }
}

function Docker-Exec-Sh {
  param(
    [Parameter(Mandatory)][string]$Container,
    [Parameter(Mandatory)][string]$ShellCommand
  )
  Exec -File "docker" -Args @("exec","-i",$Container,"sh","-lc",$ShellCommand)
}

# Main
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-RepoRoot -Start $scriptRoot
$flowsPath = Resolve-Path (Join-Path $repoRoot $FlowsDir) -ErrorAction SilentlyContinue
if (-not $flowsPath) {
  $flowsPath = Join-Path $repoRoot "flows"
  New-Item -ItemType Directory -Force -Path $flowsPath | Out-Null
}
$flowsPath = Resolve-Path $flowsPath

Write-Host "[i] Repo root: $repoRoot" -ForegroundColor Yellow
Write-Host "[i] Flows dir: $flowsPath" -ForegroundColor Yellow
Write-Host "[i] Container: $Container" -ForegroundColor Yellow
Write-Host "[i] Mode: $Mode" -ForegroundColor Yellow

Push-Location $repoRoot
try {
  # Quick container sanity
  Exec -File "docker" -Args @("ps","--format","table {{.Names}}\t{{.Status}}")
  Exec -File "docker" -Args @("inspect","$Container","--format","{{.Name}}") | Out-Null

  if ($Mode -eq "export") {
    # Export from container to host
    $tmpInContainer = "/tmp/n8n_export"
    Write-Host "[i] Preparing container export dir $tmpInContainer" -ForegroundColor Cyan
    Docker-Exec-Sh -Container $Container -ShellCommand "rm -rf $tmpInContainer && mkdir -p $tmpInContainer"

    Write-Host "[i] Running: n8n export:workflow --all --pretty --separate --output $tmpInContainer" -ForegroundColor Cyan
    Docker-Exec-Sh -Container $Container -ShellCommand "n8n export:workflow --all --pretty --separate --output $tmpInContainer"

    # Copy back to host flows dir
    $dest = Join-Path $flowsPath "export"
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    $containerSrc = Join-ContainerPath -Container $Container -Path $tmpInContainer
    Exec -File "docker" -Args @("cp", $containerSrc, "$dest")

    Write-Host "[ok] Export completed -> $dest" -ForegroundColor Green
    Write-Host "[i] Files:" -ForegroundColor DarkGray
    Get-ChildItem -Path $dest -Recurse -File | Select-Object -First 10 | ForEach-Object { Write-Host " - $($_.FullName)" }

  } elseif ($Mode -eq "import") {
    # Import from host to container
    $srcDir = $flowsPath
    if (-not (Get-ChildItem -Path $srcDir -Recurse -Include *.json -File -ErrorAction SilentlyContinue)) {
      throw "No .json workflow files found under $srcDir. Place exported workflows there (or under $srcDir\export) and retry."
    }

    $tmpInContainer = "/tmp/n8n_import"
    Write-Host "[i] Preparing container import dir $tmpInContainer" -ForegroundColor Cyan
    Docker-Exec-Sh -Container $Container -ShellCommand "rm -rf $tmpInContainer && mkdir -p $tmpInContainer"

    # If user has 'flows\export' use its content; else use flows root
    $importSource = (Test-Path (Join-Path $srcDir "export")) ? (Join-Path $srcDir "export") : $srcDir
    Write-Host "[i] Copying workflows from $importSource to container..." -ForegroundColor Cyan
    Exec -File "docker" -Args @("cp", "$importSource", (Join-ContainerPath -Container $Container -Path $tmpInContainer))

    Write-Host "[i] Running: n8n import:workflow --separate --input $tmpInContainer/export" -ForegroundColor Cyan
    # Try both /tmp/n8n_import/export and /tmp/n8n_import (depending on what we copied)
    $cmd = @"
if [ -d '$tmpInContainer/export' ]; then
  n8n import:workflow --separate --input '$tmpInContainer/export';
else
  n8n import:workflow --separate --input '$tmpInContainer';
fi
"@
    Docker-Exec-Sh -Container $Container -ShellCommand $cmd

    Write-Host "[ok] Import completed." -ForegroundColor Green
    Write-Host "[i] Verify in n8n UI and activate the workflows." -ForegroundColor DarkGray
  } else {
    throw "Unsupported mode: $Mode"
  }

} catch {
  Write-Host "[error] $($_.Exception.Message)" -ForegroundColor Red
  throw
} finally {
  Pop-Location
}
