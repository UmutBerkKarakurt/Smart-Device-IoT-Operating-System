"""OS-style logging with component prefixes."""

from __future__ import annotations

import sys
from typing import TextIO

_VALID_COMPONENTS = frozenset(
    {
        "Scheduler",
        "Process",
        "Memory",
        "FileSystem",
        "Concurrency",
        "Simulation",
    }
)


def log(component: str, message: str, *, stream: TextIO | None = None) -> None:
    """Write a single line with prefix ``[Component]``."""
    if component not in _VALID_COMPONENTS:
        component = "Simulation"
    out = stream if stream is not None else sys.stdout
    out.write(f"[{component}] {message}\n")
    out.flush()


def get_logger(component: str):
    """Return a callable ``(msg: str) -> None`` bound to ``component``."""

    def _emit(message: str) -> None:
        log(component, message)

    return _emit
