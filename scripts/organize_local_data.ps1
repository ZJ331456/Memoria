# Run with Memoria stopped, after reserving qin. Preserve all existing personal data.
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$dataRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot 'data')).TrimEnd('\')
$moves = @(
    @('memoria.db', 'legacy/pre-users/memoria.db'),
    @('memoria.db-wal', 'legacy/pre-users/memoria.db-wal'),
    @('memoria.db-shm', 'legacy/pre-users/memoria.db-shm'),
    @('tmp_local_llm_debug.db', 'legacy/pre-users/tmp_local_llm_debug.db'),
    @('markdown', 'legacy/pre-users/markdown'),
    @('backups', 'legacy/pre-users/backups'),
    @('mcp_servers.json', 'legacy/pre-users/mcp_servers.json'),
    @('models.override.toml', 'config/models.override.toml'),
    @('datasets/lccc-base/multiturn-50.db', 'legacy/public-corpus/multiturn-50.db'),
    @('datasets/lccc-base/multiturn-50.import.json', 'legacy/public-corpus/multiturn-50.import.json'),
    @('reports/local_embedding_environment.json', 'reports/system/local_embedding_environment.json'),
    @('reports/mcp-check.json', 'reports/system/mcp-check-before-isolation.json'),
    @('benchmark', 'legacy/research-license'),
    @('mcp_workspace', 'legacy/mcp_workspace')
)
# Resolve every target before any directory move. No shell switching or deletion.
foreach ($entry in $moves) {
    foreach ($relative in $entry) {
        $absolute = [IO.Path]::GetFullPath((Join-Path $dataRoot $relative))
        if (-not $absolute.StartsWith($dataRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Move escapes data root' }
    }
}
foreach ($entry in $moves) {
    $source = [IO.Path]::GetFullPath((Join-Path $dataRoot $entry[0]))
    $destination = [IO.Path]::GetFullPath((Join-Path $dataRoot $entry[1]))
    if (-not (Test-Path -LiteralPath $source)) { continue }
    if (Test-Path -LiteralPath $destination) { throw ('Existing destination: ' + $entry[1]) }
    if ((Get-Item -LiteralPath $source).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing reparse point move' }
    [void](New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force)
    Move-Item -LiteralPath $source -Destination $destination
    Write-Output ($entry[0] + ' -> ' + $entry[1])
}
& (Join-Path $PSScriptRoot 'protect_local_data.ps1')
