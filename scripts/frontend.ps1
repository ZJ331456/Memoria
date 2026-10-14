[CmdletBinding()]
param([switch]$Install)
$ErrorActionPreference = 'Stop'
$taskFrontend = Join-Path (Split-Path $PSScriptRoot -Parent) 'frontend'
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Node.js/npm is missing. Install it before building the frontend.' }
Push-Location -LiteralPath $taskFrontend
try {
    if ($Install -or -not (Test-Path -LiteralPath 'node_modules' -PathType Container)) {
        & npm.cmd ci --cache .npm-cache --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend type check or build failed.' }
    Write-Host 'Built Chat (/) and Memory Studio (/studio/). Start or restart the backend with scripts\cmd\start_backend.cmd.'
} finally { Pop-Location }
