"""Callbacks for deterministic sync primitives (no Simulation import from concurrency)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from concurrency.locks import Lock


@dataclass
class SyncPorts:
    """Small port surface wired from ``Simulation`` (mirrors Phase 2 ``MemoryPorts`` idea)."""

    block_process: Callable[[int], None]
    wake_process: Callable[[int], None]
    log_scheduler: Optional[Callable[[str], None]] = None
    # Phase 5: optional priority inheritance (Phase 3 leaves these as None).
    recompute_mutex_inheritance: Optional[Callable[["Lock"], None]] = None
    clear_mutex_inheritance_for_holder: Optional[Callable[[int], None]] = None
    # Final phase: optional observability hooks (no effect when None).
    on_semaphore_blocked: Optional[Callable[[str, int], None]] = None
    on_semaphore_signal_handoff: Optional[Callable[[str, int, int], None]] = None
    on_mutex_blocked: Optional[Callable[[str, int, int], None]] = None
