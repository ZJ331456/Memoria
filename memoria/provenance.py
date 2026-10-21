"""Server-assigned message ingress, independent of titles, text and client headers."""
from contextlib import contextmanager
from contextvars import ContextVar
import re

CHANNELS = {'chat','api','feishu','qq','telegram','internal'}
current_ingress: ContextVar[dict | None] = ContextVar('memoria_message_ingress',default=None)


@contextmanager
def message_ingress(channel: str, event_ref: str | None = None, *, preserve=False,source_time: str | None = None):
    if channel not in CHANNELS or (event_ref is not None and not re.fullmatch(r'[0-9a-f]{64}',event_ref)):
        raise ValueError('Invalid server message ingress')
    value=current_ingress.get() if preserve and current_ingress.get() else {'channel':channel,'event_ref':event_ref}
    if source_time is not None and not (preserve and current_ingress.get()):
        from .memory.observation_semantics import aware_time
        value={**value,'source_time':aware_time(source_time),
            'source_time_basis':'platform_event' if channel in {'feishu','qq','telegram'} else 'declared_original'}
    token=current_ingress.set(value)
    try:yield
    finally:current_ingress.reset(token)
