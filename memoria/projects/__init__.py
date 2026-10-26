"""DevMemory business layer: project workspaces over the untouched memory core.

Sources stay typed library entries (kind/authorship/scope); membership, tasks and
events live in project_* tables owned here. Every memory read/write goes through
the MemoryKernel facade — this package never touches memoria/memory internals.
"""

from .errors import ProjectError
from .git_source import collect_git_history
from .workspace import ProjectWorkspaces

__all__ = ["ProjectError", "ProjectWorkspaces", "collect_git_history"]
