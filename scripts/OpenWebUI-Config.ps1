[CmdletBinding()]
param(
    [ValidateSet('export','import')]
    [string]$Command = 'export',
    [string]$ComposeFile = 'compose/docker-compose.yml',
    [string]$Service = 'openwebui',
    [string]$DumpPath = 'openwebui/export/app.db.sql'
)

$ErrorActionPreference = 'Stop'

function Invoke-DockerCompose {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        $joined = $Arguments -join ' '
        throw "docker $joined failed with exit code $LASTEXITCODE"
    }
    return $output
}

$composeFilePath = (Resolve-Path -Path $ComposeFile).Path
$composeArgs = @('compose','-f',$composeFilePath)

switch ($Command) {
    'export' {
        $dumpDirectory = Split-Path -Path $DumpPath -Parent
        if ([string]::IsNullOrWhiteSpace($dumpDirectory)) {
            $dumpDirectory = '.'
        }
        if (-not (Test-Path -Path $dumpDirectory)) {
            New-Item -ItemType Directory -Path $dumpDirectory | Out-Null
        }

        $pythonExport = @'
import sqlite3, sys, os
from pathlib import Path

db_path = Path("/app/backend/data/app.db")
if not db_path.exists():
    sys.stderr.write(f"Database {db_path} was not found.\n")
    sys.exit(1)
conn = sqlite3.connect(db_path)
try:
    data = "\n".join(conn.iterdump())
finally:
    conn.close()
sys.stdout.write(data)
'@

        $command = @"
python - <<'PY'
$pythonExport
PY
"@

        $execArgs = $composeArgs + @('exec','-T',$Service,'sh','-lc',$command)
        $result = Invoke-DockerCompose -Arguments $execArgs
        if ($result -is [System.Array]) {
            $content = $result -join [Environment]::NewLine
        } else {
            $content = [string]$result
        }
        if (-not $content.EndsWith("`n")) {
            $content += [Environment]::NewLine
        }
        Set-Content -Path $DumpPath -Value $content -Encoding UTF8
        Write-Host "Exported Open WebUI database to $DumpPath"
    }
    'import' {
        if (-not (Test-Path -Path $DumpPath)) {
            throw "Dump file '$DumpPath' was not found. Run export first."
        }

        $resolvedDump = (Resolve-Path -Path $DumpPath).Path
        $containerTarget = "$Service`:/tmp/openwebui.sql"
        $cpArgs = $composeArgs + @('cp', $resolvedDump, $containerTarget)
        Invoke-DockerCompose -Arguments $cpArgs | Out-Null

        $pythonImport = @'
import sqlite3, os
from pathlib import Path

db_path = Path("/app/backend/data/app.db")
tmp_path = Path("/tmp/openwebui.sql")
if not tmp_path.exists():
    raise SystemExit("Temporary SQL dump missing inside container")
db_path.parent.mkdir(parents=True, exist_ok=True)
if db_path.exists():
    os.remove(db_path)
with tmp_path.open("r", encoding="utf-8") as handle:
    sql = handle.read()
conn = sqlite3.connect(db_path)
try:
    conn.executescript(sql)
finally:
    conn.close()
os.remove(tmp_path)
'@

        $command = @"
python - <<'PY'
$pythonImport
PY
"@
        $execArgs = $composeArgs + @('exec','-T',$Service,'sh','-lc',$command)
        Invoke-DockerCompose -Arguments $execArgs | Out-Null
        Write-Host "Imported Open WebUI database from $DumpPath"
    }
}
