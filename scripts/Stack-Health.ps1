[CmdletBinding()]
param(
  [string]$Service = ""
)

$ErrorActionPreference = "Stop"

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

function Check-Http {
  param([string]$Name,[string]$Url,[int]$TimeoutSec = 5)
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) {
      Say "[ok] ${Name}: $($r.StatusCode)" "Green"; return $true
    }
    Say "[!] ${Name}: HTTP $($r.StatusCode)" "Yellow"; return $false
  } catch {
    Say "[x] ${Name}: $($_.Exception.Message)" "Red"; return $false
  }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Error "Docker is not available in PATH."; exit 1
}

$targets = @(
  @{ name='searxng';   container='searxng';  port=8080; path='/';          mode='http' },
  @{ name='webfetch';  container='webfetch'; port=8081; path='/health';     mode='http' },
  @{ name='qdrant';    container='qdrant';   port=6333; path='/healthz';    mode='http' },
  @{ name='embedder';  container='embedder'; port=8082; path='/health';     mode='http' },
  @{ name='n8n';       container='n8n';      port=5678; path='/healthz';    mode='http' },
  # ragproxy is internal; check via docker exec
  @{ name='ragproxy';  container='ragproxy'; port=4080; path='/health';     mode='exec' },
  # Skip LiteLLM by default to avoid spinning up GPU due to probing through Ollama
  @{ name='openwebui'; container='openwebui';port=8080; path='/';           mode='http' }
)

if ($Service) { $targets = $targets | Where-Object { $_.name -eq $Service } }

$okAll = $true
foreach ($t in $targets) {
  if ($t.mode -eq 'http') {
    $hp = Get-MappedPort -ContainerName $t.container -InternalPort $t.port
    $url = "http://localhost:$hp$($t.path)"
    $ok = Check-Http -Name $t.name -Url $url
    if (-not $ok) { $okAll = $false }
  } elseif ($t.mode -eq 'exec') {
    try {
      $cmd = "wget -qO- http://localhost:$($t.port)$($t.path)"
      $out = docker exec $($t.container) sh -lc $cmd 2>$null
      if ($LASTEXITCODE -eq 0 -and $out) {
        Say "[ok] $($t.name): healthy" "Green"
      } else { throw "exec failed" }
    } catch {
      Say "[x] $($t.name): $($_.Exception.Message)" "Red"; $okAll = $false
    }
  }
}

if (-not $okAll) { exit 1 }
exit 0
