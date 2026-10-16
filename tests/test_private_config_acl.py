"""Exercise both Windows PowerShell ACL APIs in an isolated temporary project."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name!='nt',reason='Windows NTFS protection')
@pytest.mark.parametrize('shell',['powershell.exe','pwsh.exe'])
def test_private_config_and_data_acl_need_no_security_privilege(tmp_path,shell):
    executable=shutil.which(shell)
    if not executable:pytest.skip('Shell unavailable')
    scripts=tmp_path/'scripts';scripts.mkdir()
    shutil.copy2(ROOT/'scripts/protect_local_data.ps1',scripts/'protect.ps1')
    for branch in ('data','config'):
        folder=tmp_path/branch;folder.mkdir(parents=True)
        (folder/'private.fixture').write_text('engineering fixture only')
    result=subprocess.run([executable,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(scripts/'protect.ps1')],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'Protected private storage items: 4' in result.stdout
    command=r'''$taskOwner=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value;
    foreach($taskPath in @('data','config','config/private.fixture')) {
      $taskItem=Get-Item -LiteralPath $taskPath;
      $taskAcl=if($PSVersionTable.PSEdition -eq 'Core') { [IO.FileSystemAclExtensions]::GetAccessControl($taskItem) } else { $taskItem.GetAccessControl() };
      $taskRules=$taskAcl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]);
      if(-not $taskAcl.AreAccessRulesProtected -or $taskRules.Count -ne 2) { throw 'Unexpected private ACL'; }
      foreach($taskRule in $taskRules) {
        $taskSid=$taskRule.IdentityReference.Value;
        if($taskSid -notin @($taskOwner,'S-1-5-18')) { throw 'Unexpected private principal'; }
      }
    }'''
    checked=subprocess.run([executable,'-NoProfile','-Command',command],cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert checked.returncode==0,checked.stderr
