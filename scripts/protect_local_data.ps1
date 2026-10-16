# Local administrator utility: restrict data to the current Windows identity and SYSTEM.
param([string]$Directory)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$taskPrivateTargets = if ($Directory) { @($Directory) } else { @((Join-Path $repoRoot 'data'), (Join-Path $repoRoot 'configs\local')) }
$taskProtectedCount = 0
foreach ($taskPrivateTarget in $taskPrivateTargets) {
if (-not (Test-Path -LiteralPath $taskPrivateTarget)) { continue }
$target = [IO.Path]::GetFullPath($taskPrivateTarget).TrimEnd('\')
if (-not $target.StartsWith($repoRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Private directory must remain inside the repository'
}
$ownerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$items = @((Get-Item -LiteralPath $target)) + @(Get-ChildItem -LiteralPath $target -Recurse -Force)
foreach ($item in $items) {
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing reparse points in private storage' }
    $acl = if ($item.PSIsContainer) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = if ($item.PSIsContainer) { 'ContainerInherit,ObjectInherit' } else { 'None' }
    foreach ($sid in @($ownerSid, $systemSid)) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', $inherit, 'None', 'Allow'))
    }
    if ($PSVersionTable.PSEdition -eq 'Core') {
        [IO.FileSystemAclExtensions]::SetAccessControl($item, $acl)
    } else {
        # Windows PowerShell 5 has the instance API, without asking Set-Acl to
        # write auditing/ownership sections requiring SeSecurityPrivilege.
        $item.SetAccessControl($acl)
    }
}
$taskProtectedCount += $items.Count
}
Write-Output ('Protected private storage items: ' + $taskProtectedCount)
