"""Windows launcher safety checks; no model calls or production server startup."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
POWERSHELL=shutil.which('powershell.exe')
CMD=shutil.which('cmd.exe')
pytestmark=pytest.mark.skipif(os.name!='nt' or not POWERSHELL or not CMD,reason='Windows CMD/PowerShell launcher')


def invoke(*args, cwd=ROOT, entry='start_backend.cmd', direct=False):
    # A Windows background child can inherit a captured pipe and postpone EOF
    # after CMD exits. File capture waits for the launcher, not the live service.
    with tempfile.TemporaryFile('w+',encoding='utf-8',errors='replace') as output, tempfile.TemporaryFile('w+',encoding='utf-8',errors='replace') as errors:
        command=([POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/backend.ps1'),*args]
                 if direct else [CMD,'/d','/c',str(ROOT/'scripts/cmd'/entry),*args])
        result=subprocess.run(command,
            cwd=cwd,stdin=subprocess.DEVNULL,stdout=output,stderr=errors,text=True,timeout=30)
        output.seek(0);errors.seek(0)
        result.stdout,result.stderr=output.read(),errors.read()
        return result


def test_occupied_foreign_port_is_not_replaced_or_stopped(tmp_path):
    config=tmp_path/'private config.toml';config.write_text('',encoding='utf-8')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        port=listener.getsockname()[1]
        assert not (ROOT/f'data/run/backend-{port}.json').exists()
        result=invoke('-Port',str(port),'-Config',str(config),cwd=tmp_path,direct=True)
        assert result.returncode!=0 and 'already occupied' in result.stderr
        assert listener.getsockname()[1]==port
        assert not (ROOT/f'data/run/backend-{port}.json').exists()


def test_stale_pid_record_cannot_stop_another_process():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    state=ROOT/f'data/run/backend-{port}.json'
    state.parent.mkdir(parents=True,exist_ok=True)
    with state.open('x',encoding='utf-8') as stream:
        json.dump({'process_id':os.getpid(),'start_ticks':'0','command_line':'',
                   'main':str(ROOT/'main.py'),'port':port},stream)
    try:
        result=invoke('-Port',str(port),entry='stop_backend.cmd')
        assert result.returncode!=0 and 'ownership check failed' in result.stderr
        assert state.exists()
    finally:state.unlink()


def test_cmd_status_resolves_repository_from_another_directory(tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    assert not (ROOT/f'data/run/backend-{port}.json').exists()
    result=invoke('-Action','Status','-Port',str(port),cwd=tmp_path)
    assert result.returncode==0
    assert 'No managed backend is running.' in result.stdout


def test_start_validates_models_from_another_directory_and_stops_owned_service(tmp_path):
    import json
    from urllib.request import urlopen
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    config=tmp_path/'private service.toml'
    config.write_text('[storage]\ndatabase="'+(tmp_path/'state/memoria.db').as_posix()+'"\n[agent.mcp]\nenabled=false\n')
    logs=ROOT/'data/logs/system'
    original=set(logs.glob(f'backend-{port}-*.log')) if logs.exists() else set()
    try:
        result=invoke('-Port',str(port),'-Config',str(config),cwd=tmp_path)
        assert result.returncode==0,result.stderr
        with urlopen(f'http://127.0.0.1:{port}/api/health',timeout=5) as response:
            assert json.load(response)['login_required'] is True
    finally:
        stopped=invoke('-Port',str(port),entry='stop_backend.cmd',cwd=tmp_path)
        assert stopped.returncode==0,stopped.stderr
        for file in set(logs.glob(f'backend-{port}-*.log'))-original:
            assert file.resolve().is_relative_to(logs.resolve())
            file.unlink()
    assert not (ROOT/f'data/run/backend-{port}.json').exists()
