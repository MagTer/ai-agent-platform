<# 
.N8N-Workflows.ps1
Import/export n8n workflows between the running container and the repository.

Usage:
  .\N8N-Workflows.ps1 import        # repo -> container
  .\N8N-Workflows.ps1 export        # container -> repo
  .\N8N-Workflows.ps1 -Mode import -Container n8n -FlowsDir "..\flows"

Notes:
- ASCII only. Works from any folder (Push-Location/Pop-Location).
- Uses n8n CLI inside the container:
    n8n import:workflow --separate --input <dir>
    n8n export:workflow --all --pretty --separate --output <dir>
- Default container: 'n8n'. Default flows dir: '../flows'
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
    if (Test-Path $compose) { return (Split-Path (Split-Path $compose -Parent) -Parent) }
    $parent = Split-Path $dir -Parent
    if ($parent -eq $dir) { break }
    $dir = $parent
  }
  throw "Could not find compose\docker-compose.yml upwards from $Start"
}

function Join-ContainerPath {
  param([Parameter(Mandatory)][string]$Container,
        [Parameter(Mandatory)][string]$Path)
  if (-not $Path.StartsWith("/")) { $Path = "/$Path" }
  return "${Container}:$Path"
}

function Exec {
  param([Parameter(Mandatory)][string]$File,
        [Parameter()][string[]]$Args = @())
  Write-Host ">> $File $($Args -join ' ')" -ForegroundColor DarkGray
  $output = & $File @Args 2>&1
  $code = $LASTEXITCODE
  if ($code -ne 0) {
    if ($output) { Write-Host $output -ForegroundColor Red }
    throw "Command failed: $File $($Args -join ' ') (exit $code)"
  }
  if ($output) { Write-Host $output }
  return $output
}

function Docker-Exec-Sh {
  param([Parameter(Mandatory)][string]$Container,
        [Parameter(Mandatory)][string]$ShellCommand,
        [Parameter()][string]$User = "")
  $args = @("exec")
  if ($User) { $args += @("-u",$User) }
  $args += @("-i",$Container,"sh","-lc",$ShellCommand)
  Exec "docker" $args | Out-Null
}

# ---- Main ----
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot   = Resolve-RepoRoot -Start $scriptRoot

# Resolve flows dir
$flowsPathCandidate = Join-Path $repoRoot $FlowsDir
if (-not (Test-Path $flowsPathCandidate)) {
  $flowsPathCandidate = Join-Path $repoRoot "flows"
  New-Item -ItemType Directory -Force -Path $flowsPathCandidate | Out-Null
}
$flowsPath = Resolve-Path $flowsPathCandidate

Write-Host "[i] Repo root: $repoRoot" -ForegroundColor Yellow
Write-Host "[i] Flows dir: $flowsPath" -ForegroundColor Yellow
Write-Host "[i] Container: $Container" -ForegroundColor Yellow
Write-Host "[i] Mode: $Mode" -ForegroundColor Yellow

Push-Location $repoRoot
try {
  # Container exists?
  Exec "docker" @("inspect",$Container,"--format","{{.Name}}") | Out-Null

  # Use tmp dirs under /home/node to minimize permission issues
  $tmpExport = "/home/node/n8n_export"
  $tmpImport = "/home/node/n8n_import"

  if ($Mode -eq "export") {
    # Prepare export dir (as root, then chown to node)
    Write-Host "[i] Preparing container export dir $tmpExport" -ForegroundColor Cyan
    Docker-Exec-Sh -Container $Container -User "0" -ShellCommand "rm -rf '$tmpExport' && mkdir -p '$tmpExport' && chown -R node:node '$tmpExport'"

    Write-Host "[i] Running export in container" -ForegroundColor Cyan
    # Tolerate 'No workflows found'
    Docker-Exec-Sh -Container $Container -ShellCommand "n8n export:workflow --all --pretty --separate --output '$tmpExport' || true"

    # Count exported files
    $countOut = Exec "docker" @("exec","-i",$Container,"sh","-lc","ls -1 $tmpExport/*.json 2>/dev/null | wc -l")
    $count = [int]($countOut.Trim())

    $dest = Join-Path $flowsPath "export"
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    if ($count -gt 0) {
      $containerSrc = Join-ContainerPath -Container $Container -Path $tmpExport
      Exec "docker" @("cp", $containerSrc, "$dest") | Out-Null
      Write-Host "[ok] Exported $count workflow file(s) -> $dest" -ForegroundColor Green
      Write-Host "[i] Files (top 10):" -ForegroundColor DarkGray
      Get-ChildItem -Path $dest -Recurse -File | Select-Object -First 10 | ForEach-Object { Write-Host " - $($_.FullName)" }
    } else {
      Write-Host "[i] No workflows in container. Created empty '$dest'." -ForegroundColor Yellow
    }

  } elseif ($Mode -eq "import") {
    # Prefer flows/export if present
    $exportSub = Join-Path $flowsPath "export"
    if (Test-Path $exportSub) { $importSource = $exportSub } else { $importSource = $flowsPath }

    if (-not (Get-ChildItem -Path $importSource -Recurse -Include *.json -File -ErrorAction SilentlyContinue)) {
      throw "No .json workflow files found under $importSource. Place exported workflows there (or under flows/export) and retry."
    }

    # Prepare import dir as root, then chown to node
    Write-Host "[i] Preparing container import dir $tmpImport" -ForegroundColor Cyan
    Docker-Exec-Sh -Container $Container -User "0" -ShellCommand "rm -rf '$tmpImport' && mkdir -p '$tmpImport' && chown -R node:node '$tmpImport'"

    Write-Host "[i] Copying workflows from $importSource to container..." -ForegroundColor Cyan
    Exec "docker" @("cp", "$importSource", (Join-ContainerPath -Container $Container -Path $tmpImport)) | Out-Null

    # Fix ownership after cp (docker cp sets root)
    Docker-Exec-Sh -Container $Container -User "0" -ShellCommand "chown -R node:node '$tmpImport'"

    Write-Host "[i] Importing via n8n CLI" -ForegroundColor Cyan
    $single = "if [ -d '$tmpImport/export' ]; then n8n import:workflow --separate --input '$tmpImport/export'; else n8n import:workflow --separate --input '$tmpImport'; fi"
    Docker-Exec-Sh -Container $Container -ShellCommand $single

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
