"""CA cache behavior; temporary files are not model/benchmark data."""
import asyncio
import ssl
from pathlib import Path

import pytest
from memoria.llm import tls


def test_ca_cache_rotation_and_environment_precedence(tmp_path,monkeypatch):
    bundle=tmp_path/'ca.pem';bundle.write_text('temporary engineering CA metadata')
    alternative=tmp_path/'other.pem';alternative.write_text('another temporary CA metadata')
    called=[]
    class Context:
        check_hostname=True
        verify_mode=ssl.CERT_REQUIRED
    def create(**kwargs):called.append(kwargs);return Context()
    monkeypatch.setattr(tls.ssl,'create_default_context',create)
    monkeypatch.setenv('SSL_CERT_FILE',str(bundle))
    monkeypatch.setenv('SSL_CERT_DIR',str(tmp_path))
    import certifi
    monkeypatch.setattr(certifi,'where',lambda:str(alternative))
    tls._load.cache_clear()
    first=tls.verified_context()
    assert tls.verified_context() is first and len(called)==1
    assert called[0]=={'cafile':str(bundle.resolve())}
    assert tls.verified_context(False) is not first
    bundle.write_text('changed temporary CA metadata with new size')
    assert tls.verified_context() is not first
    monkeypatch.delenv('SSL_CERT_FILE')
    tls.verified_context()
    assert called[-1]=={'capath':str(tmp_path.resolve())}
    count=len(called);(tmp_path/'extra.pem').write_text('new temporary entry')
    tls.verified_context()
    assert len(called)==count+1
    tls._load.cache_clear()


def test_cold_context_does_not_block_async_loop(monkeypatch):
    import time
    marker=[]
    def slow(trust):time.sleep(.08);return 'temporary'
    monkeypatch.setattr(tls,'verified_context',slow)
    async def run():
        work=asyncio.create_task(tls.async_verified_context())
        await asyncio.sleep(.01)
        marker.append(not work.done())
        assert await work=='temporary'
    asyncio.run(run())
    assert marker==[True]


def test_invalid_ca_fails_closed(tmp_path,monkeypatch):
    monkeypatch.setenv('SSL_CERT_FILE',str(tmp_path/'missing.pem'))
    with pytest.raises(FileNotFoundError):tls.verified_context()
