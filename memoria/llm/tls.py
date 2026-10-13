"""Reuse CA contexts without sharing HTTP sessions, credentials or disabling verification."""
from __future__ import annotations
import asyncio
import os
import ssl
from functools import lru_cache
from pathlib import Path
from threading import Lock

_LOCK = Lock()


def _identity(path: Path):
    stat = path.stat()
    if path.is_dir():
        entries = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(path.iterdir()) if p.is_file())
    else:
        entries = ()
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, entries)


@lru_cache(maxsize=8)
def _load(kind, identity):
    context = ssl.create_default_context(**{kind: identity[0]})
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
        raise RuntimeError('TLS certificate verification is required')
    return context


def verified_context(trust_env=True):
    # Match HTTPX's verify=True precedence exactly; changing an environment CA
    # path/file/directory creates a fresh context. No API/profile data is cached.
    import certifi
    if trust_env and os.environ.get('SSL_CERT_FILE'):
        kind, path = 'cafile', Path(os.environ['SSL_CERT_FILE'])
    elif trust_env and os.environ.get('SSL_CERT_DIR'):
        kind, path = 'capath', Path(os.environ['SSL_CERT_DIR'])
    else:
        kind, path = 'cafile', Path(certifi.where())
    identity = _identity(path)
    with _LOCK:
        return _load(kind, identity)


async def async_verified_context(trust_env=True):
    # Cold CA loading is also off the asyncio loop (not just cached after first use).
    return await asyncio.to_thread(verified_context, trust_env)
