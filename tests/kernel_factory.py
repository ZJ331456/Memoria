"""Isolated tool composition; never reads the real model or account configuration."""

from pathlib import Path
from memoria.config import Settings
from memoria.memory.kernel import MemoryKernel


def tool_kernel(store, memory=None, claims=None):
    directory = Path(store.db.execute("PRAGMA database_list").fetchone()[2]).parent
    config = directory / "tool-kernel.toml"
    config.write_text(
        '[storage]\ndatabase="' + (directory / "test.db").as_posix() + '"\n'
        '[memory.markdown]\nenabled=false\n[llm.main]\nmodel="engineering-only"\n'
        'base_url="https://model.invalid/v1"\napi_key="engineering-only"\n',
        encoding="utf-8",
    )
    kernel = MemoryKernel(
        Settings.load(config), store, user_id=claims.user_id if claims else "local"
    )
    if memory is not None:
        kernel.memory = memory
        kernel.personal_access.engine = memory
        kernel.queries.memory = memory
    return kernel
