[CmdletBinding()]
param(
  [string]$Host = 'localhost',
  [int]$Port = 6333,
  [string]$Collection = 'memory',
  [int]$Size = 384,
  [ValidateSet('Cosine','Euclid','Dot')]
  [string]$Distance = 'Cosine',
  [switch]$Recreate
)

$ErrorActionPreference = 'Stop'

function Get-Url { param([string]$path) return "http://$Host`:$Port$path" }

function Say($m,$c='Cyan'){ Write-Host $m -ForegroundColor $c }

try {
  $exists = $false
  try {
    $r = Invoke-RestMethod -Method GET -Uri (Get-Url "/collections/$Collection") -TimeoutSec 5
    if ($r.status -eq 'ok') { $exists = $true }
  } catch {
    $exists = $false
  }

  if ($exists -and $Recreate) {
    Say "[i] Deleting existing collection '$Collection'" 'Yellow'
    Invoke-RestMethod -Method DELETE -Uri (Get-Url "/collections/$Collection") -TimeoutSec 15 | Out-Null
    $exists = $false
  }

  if (-not $exists) {
    Say "[i] Creating collection '$Collection' (size=$Size, distance=$Distance)" 'Yellow'
    $body = @{ vectors = @{ size = $Size; distance = $Distance } } | ConvertTo-Json -Compress
    Invoke-RestMethod -Method PUT -Uri (Get-Url "/collections/$Collection") -ContentType 'application/json' -Body $body -TimeoutSec 30 | Out-Null
    Say "[ok] Collection created." 'Green'
  } else {
    Say "[ok] Collection '$Collection' exists (skipping create)." 'Green'
  }
} catch {
  Say "[error] $($_.Exception.Message)" 'Red'
  exit 1
}

exit 0

