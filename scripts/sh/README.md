# Linux shell entry points (reserved)

This directory is reserved for future Linux shell scripts. No Linux launcher is implemented or verified yet.

Planned entry points mirror [Windows CMD](../cmd/README.md): frontend build, backend start/status, and backend stop. They must resolve the repository from their own location, use `.venv/bin/python`, and preserve account/configuration isolation and process ownership checks.

Python dependencies stay in the root [requirements.txt](../../requirements.txt). Shared Python tools stay directly under `scripts/`; platform entry points belong in `scripts/cmd/` or `scripts/sh/`.
