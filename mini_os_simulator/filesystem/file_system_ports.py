"""Callbacks for deterministic file I/O blocking (no Simulation import from filesystem)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class FileSystemPorts:
    """Wired from ``Simulation`` — mirrors ``MemoryPorts`` / ``SyncPorts`` pattern."""

    block_process: Callable[[int], None]
    wake_process: Callable[[int], None]
    log_scheduler: Optional[Callable[[str], None]] = None
