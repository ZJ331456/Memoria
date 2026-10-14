[CmdletBinding()]
param(
    [ValidateSet('Start','Status','Stop')][string]$Action = 'Start',
    [ValidateRange(1024,65535)][int]$Port = 2237,
    [string]$Config = 'config.toml',
    [ValidateRange(1,60)][int]$TimeoutSeconds = 30
)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskMain = Join-Path $taskRoot 'main.py'
$taskConfig = if ([IO.Path]::IsPathRooted($Config)) { [IO.Path]::GetFullPath($Config) } else { [IO.Path]::GetFullPath((Join-Path $taskRoot $Config)) }
$taskRun = Join-Path $taskRoot 'data\run'
$taskLogs = Join-Path $taskRoot 'data\logs\system'
$taskStateFile = Join-Path $taskRun "backend-$Port.json"
$taskUrl = "http://127.0.0.1:$Port"

function Get-OwnedBackend {
    if (-not (Test-Path -LiteralPath $taskStateFile)) { return $null }
    $taskState = Get-Content -LiteralPath $taskStateFile -Raw | ConvertFrom-Json
    $taskProcess = Get-Process -Id $taskState.process_id -ErrorAction SilentlyContinue
    if (-not $taskProcess) { return $null }
    $taskInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($taskProcess.Id)"
    if ($taskProcess.StartTime.ToUniversalTime().Ticks.ToString() -ne $taskState.start_ticks -or
        $taskInfo.CommandLine -ne $taskState.command_line -or
        $taskState.main -ne $taskMain -or $taskState.port -ne $Port) {
        throw 'PID ownership check failed; no process was stopped. Inspect data/run manually.'
    }
    return $taskProcess
}
function Test-BackendHealth {
    try {
        $taskHealth = Invoke-RestMethod -Uri "$taskUrl/api/health" -TimeoutSec 2
        return ($taskHealth.status -eq 'ok' -and $taskHealth.login_required -eq $true)
    } catch { return $false }
}

$taskHasher = [Security.Cryptography.SHA256]::Create()
$taskKey = [BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes("$taskRoot/$Port"))).Replace('-','')
$taskHasher.Dispose()
$taskMutex = New-Object System.Threading.Mutex($false, "Local\MemoriaBackend-$taskKey")
$taskLocked = $false
try {
try { $taskLocked = $taskMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $taskLocked = $true }
if (-not $taskLocked) { throw 'Another backend lifecycle command is running. Retry shortly.' }
$taskExisting = Get-OwnedBackend
if ($Action -eq 'Stop') {
    if ($taskExisting) {
        # Ownership was checked above. Stop this process tree, including the venv
        # redirector and MCP bridges/servers, without killing unrelated Python/Node.
        $taskStopOutput = & "$env:SystemRoot\System32\taskkill.exe" /PID $taskExisting.Id /T /F 2>&1
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop the complete backend process tree. Inspect the managed process before retrying.' }
        $taskExisting.WaitForExit(5000) | Out-Null
        Write-Host 'Backend stopped.'
    } else { Write-Host 'No managed backend is running.' }
    if (Test-Path -LiteralPath $taskStateFile) { Remove-Item -LiteralPath $taskStateFile }
    exit 0
}
if ($Action -eq 'Status') {
    if ($taskExisting) { Write-Host "Managed backend PID=$($taskExisting.Id); healthy=$(Test-BackendHealth); $taskUrl" }
    else { Write-Host 'No managed backend is running.' }
    exit 0
}
if ($taskExisting) {
    if (Test-BackendHealth) { Write-Host "Backend already running: $taskUrl/ (Chat); $taskUrl/studio/ (Studio); PID=$($taskExisting.Id)"; exit 0 }
    throw 'Managed process exists but health check failed. Inspect logs; use -Action Stop before restarting.'
}
if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) { throw 'Project .venv is missing; follow README environment setup.' }
if (-not (Test-Path -LiteralPath $taskConfig -PathType Leaf)) { throw 'Config file missing. Prepare private config.toml first; existing config is never overwritten.' }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already occupied. No extra process was started or stopped."
}
New-Item -ItemType Directory -Path $taskRun,$taskLogs -Force | Out-Null
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$taskOut = Join-Path $taskLogs "backend-$Port-$taskStamp.out.log"
$taskErr = Join-Path $taskLogs "backend-$Port-$taskStamp.err.log"
$taskArgs = '"{0}" --config "{1}" --host 127.0.0.1 --port {2}' -f $taskMain,$taskConfig,$Port
$taskStarted = Start-Process -FilePath $taskPython -ArgumentList $taskArgs -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $taskOut -RedirectStandardError $taskErr
Start-Sleep -Milliseconds 200
if ($taskStarted.HasExited) { throw "Backend exited. Inspect $taskErr" }
$taskStarted.Refresh()
$taskCommand = (Get-CimInstance Win32_Process -Filter "ProcessId = $($taskStarted.Id)").CommandLine
@{process_id=$taskStarted.Id; start_ticks=$taskStarted.StartTime.ToUniversalTime().Ticks.ToString(); command_line=$taskCommand; main=$taskMain; port=$Port} |
    ConvertTo-Json | Set-Content -LiteralPath $taskStateFile -Encoding UTF8
$taskDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    if ($taskStarted.HasExited) { throw "Backend exited. Inspect $taskErr" }
    if (Test-BackendHealth) {
        Write-Host "Chat ready: $taskUrl/ (PID=$($taskStarted.Id))"
        Write-Host "Memory Studio: $taskUrl/studio/"
        Write-Host "API docs: $taskUrl/docs"
        if (Test-Path -LiteralPath (Join-Path $taskRoot 'data\config\feishu-bot.json')) {
            Write-Host 'Feishu bot: privately configured channels start with this backend. Check connection and pair after login in Chat.'
        }
        Write-Host "Logs: $taskLogs"
        if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'frontend\dist\studio\index.html')) -or
            -not (Test-Path -LiteralPath (Join-Path $taskRoot 'frontend\dist\chat\index.html'))) {
            Write-Warning 'Frontend is not built. Run build_frontend.cmd from the repository root, then restart the backend.'
        }
        exit 0
    }
    Start-Sleep -Milliseconds 400
} while ((Get-Date) -lt $taskDeadline)
throw "Startup timed out; the owned process was left for diagnosis. Inspect $taskErr; use -Action Status / Stop."
} finally {
    if ($taskLocked) { $taskMutex.ReleaseMutex() }
    $taskMutex.Dispose()
}
