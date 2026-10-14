"""Windows launcher safety checks; no model calls or production server startup."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
POWERSHELL=shutil.which('powershell.exe')
pytestmark=pytest.mark.skipif(os.name!='nt' or not POWERSHELL,reason='Windows PowerShell launcher')


def invoke(*args):
    return subprocess.run([POWERSHELL,'-NoProfile','-ExecutionPolicy','Bypass','-File',
        str(ROOT/'scripts/backend.ps1'),*args],cwd=ROOT,capture_output=True,text=True,timeout=30)


def test_occupied_foreign_port_is_not_replaced_or_stopped(tmp_path):
    config=tmp_path/'private.toml';config.write_text('',encoding='utf-8')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));listener.listen()
        port=listener.getsockname()[1]
        assert not (ROOT/f'data/run/backend-{port}.json').exists()
        result=invoke('-Port',str(port),'-Config',str(config))
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
        result=invoke('-Action','Stop','-Port',str(port))
        assert result.returncode!=0 and 'ownership check failed' in result.stderr
        assert state.exists()
    finally:state.unlink()
