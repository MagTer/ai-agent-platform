<#
.SYNOPSIS
    Exporterar och importerar n8n-workflows med Docker.

.DESCRIPTION
    Scriptet synkroniserar workflows mellan den körande n8n-containern och repo:t.
    Använd kommandot "export" för att hämta workflows till git och "import" för att
    återställa dem till en n8n-instans. Flaggan -IncludeCredentials hanterar även
    credentials-filen (flows/credentials.json).

.EXAMPLE
    .\N8N-Workflows.ps1 export

.EXAMPLE
    .\N8N-Workflows.ps1 import -IncludeCredentials
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('export', 'import')]
    [string]$Command,

    [string]$Container = 'n8n',

    [string]$WorkflowsDir,

    [string]$CombinedFile,

    [switch]$IncludeCredentials
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-PathOrDefault {
    param(
        [string]$Path,
        [string]$Default
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $Default
    }

    return [System.IO.Path]::GetFullPath($Path)
}

function Ensure-ContainerRunning {
    param([string]$Name)
    $id = (& docker ps --filter "name=^$Name$" --format '{{.ID}}').Trim()
    if (-not $id) {
        throw "Containern '$Name' kör inte. Starta stacken innan du synkar workflows."
    }
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [string[]]$Args = @()
    )
    Write-Host '$' $File ($Args -join ' ')
    & $File @Args
    if ($LASTEXITCODE -ne 0) {
        throw "Kommandot $File $($Args -join ' ') misslyckades med kod $LASTEXITCODE."
    }
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        $Content
    )
    $json = $Content | ConvertTo-Json -Depth 100
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, [System.Text.Encoding]::UTF8)
}

function Read-JsonFile {
    param([string]$Path)
    return Get-Content -Raw -Encoding UTF8 -LiteralPath $Path | ConvertFrom-Json
}

function New-TempDir {
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
    New-Item -Path $tmp -ItemType Directory | Out-Null
    return $tmp
}

function Get-Slug {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) {
        return 'workflow'
    }
    $slug = $Name.ToLowerInvariant() -replace '[^a-z0-9]+', '-' -replace '^-+', '' -replace '-+$', ''
    if ([string]::IsNullOrWhiteSpace($slug)) {
        return 'workflow'
    }
    return $slug
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$WorkflowsDir = Resolve-PathOrDefault -Path $WorkflowsDir -Default (Join-Path $repoRoot 'flows/workflows')
$CombinedFile = Resolve-PathOrDefault -Path $CombinedFile -Default (Join-Path $repoRoot 'flows/workflows.json')
$CredentialsFile = Join-Path $repoRoot 'flows/credentials.json'

New-Item -ItemType Directory -Path $WorkflowsDir -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $CombinedFile) -Force | Out-Null

Ensure-ContainerRunning -Name $Container

$exportPath = '/home/node/.n8n/export/workflows.json'
$importPath = '/home/node/.n8n/import/workflows.json'
$credentialsExportPath = '/home/node/.n8n/export/credentials.json'
$credentialsImportPath = '/home/node/.n8n/import/credentials.json'

switch ($Command) {
    'export' {
        Invoke-External -File 'docker' -Args @('exec', $Container, 'mkdir', '-p', (Split-Path -Parent $exportPath))
        Invoke-External -File 'docker' -Args @('exec', $Container, 'n8n', 'export:workflow', '--all', '--output', $exportPath)

        $tmpDir = New-TempDir
        try {
            $tmpFile = Join-Path $tmpDir 'workflows.json'
            Invoke-External -File 'docker' -Args @('cp', "$Container:$exportPath", $tmpFile)
            $workflows = Read-JsonFile -Path $tmpFile
        }
        finally {
            Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
        }

        if ($null -eq $workflows -or $workflows -is [string]) {
            throw 'Exporten returnerade inte en lista av workflows.'
        }

        Write-JsonFile -Path $CombinedFile -Content $workflows

        $existing = @{}
        Get-ChildItem -Path $WorkflowsDir -Filter '*.json' -ErrorAction SilentlyContinue | ForEach-Object { $existing[$_.Name] = $_ }

        $seen = @{}
        foreach ($workflow in $workflows) {
            $name = $workflow.name
            $id = $workflow.id
            $slug = Get-Slug -Name $name
            $fileName = if ($id) { "$slug--$id.json" } else { "$slug.json" }
            $target = Join-Path $WorkflowsDir $fileName
            Write-JsonFile -Path $target -Content $workflow
            $seen[$fileName] = $true
        }

        foreach ($file in $existing.Keys) {
            if (-not $seen.ContainsKey($file)) {
                Remove-Item -LiteralPath (Join-Path $WorkflowsDir $file) -Force
            }
        }

        if ($IncludeCredentials) {
            Invoke-External -File 'docker' -Args @('exec', $Container, 'mkdir', '-p', (Split-Path -Parent $credentialsExportPath))
            Invoke-External -File 'docker' -Args @('exec', $Container, 'n8n', 'export:credentials', '--all', '--output', $credentialsExportPath)
            Invoke-External -File 'docker' -Args @('cp', "$Container:$credentialsExportPath", $CredentialsFile)
            Write-Host "Credentials exporterades till $CredentialsFile"
        }

        $count = if ($workflows -is [System.Array]) { $workflows.Count } else { ($workflows | Measure-Object).Count }
        Write-Host "Exporterade $count workflow(s) till $WorkflowsDir och $CombinedFile"
    }
    'import' {
        $workflows = @()
        $files = @(Get-ChildItem -Path $WorkflowsDir -Filter '*.json' -ErrorAction SilentlyContinue | Sort-Object Name)
        foreach ($file in $files) {
            $workflows += Read-JsonFile -Path $file.FullName
        }
        if ($workflows.Count -eq 0 -and (Test-Path -LiteralPath $CombinedFile)) {
            $data = Read-JsonFile -Path $CombinedFile
            if ($data -is [System.Collections.IEnumerable] -and $data -isnot [string]) {
                $workflows = @($data)
            }
            elseif ($null -ne $data) {
                $workflows = @($data)
            }
        }
        if ($workflows.Count -eq 0) {
            throw "Inga workflows hittades. Kör export först eller lägg JSON-filer i $WorkflowsDir."
        }

        Write-JsonFile -Path $CombinedFile -Content $workflows

        $tmpDir = New-TempDir
        try {
            $tmpFile = Join-Path $tmpDir 'workflows.json'
            Write-JsonFile -Path $tmpFile -Content $workflows
            Invoke-External -File 'docker' -Args @('cp', $tmpFile, "$Container:$importPath")
        }
        finally {
            Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
        }

        Invoke-External -File 'docker' -Args @('exec', $Container, 'n8n', 'import:workflow', '--input', $importPath)
        $count = if ($workflows -is [System.Array]) { $workflows.Count } else { ($workflows | Measure-Object).Count }
        Write-Host "Importerade $count workflow(s) till containern '$Container'"

        if ($IncludeCredentials) {
            if (-not (Test-Path -LiteralPath $CredentialsFile)) {
                throw "Credentials-filen $CredentialsFile saknas; kan inte importera."
            }
            Invoke-External -File 'docker' -Args @('cp', $CredentialsFile, "$Container:$credentialsImportPath")
            Invoke-External -File 'docker' -Args @('exec', $Container, 'n8n', 'import:credentials', '--input', $credentialsImportPath)
            Write-Host 'Credentials importerades.'
        }
    }
}
