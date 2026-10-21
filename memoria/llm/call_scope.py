"""Per-task inference ceilings; never mutate shared client configuration."""
from contextlib import contextmanager
from contextvars import ContextVar

bounded_scope = ContextVar('bounded_inference_scope', default=None)


@contextmanager
def inference_scope(observer):
    token = bounded_scope.set(observer)
    try:
        yield
    finally:
        bounded_scope.reset(token)
