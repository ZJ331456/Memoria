"""Stable public entry point for the Memoria HTTP API."""

from .app import VERSION
from .gateway import create_app

__all__ = ["VERSION", "create_app"]
