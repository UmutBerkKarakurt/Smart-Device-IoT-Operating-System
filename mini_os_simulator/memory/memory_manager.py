"""Simple contiguous logical memory accounting (no paging)."""

from __future__ import annotations

from typing import Dict

from os_core.logger import get_logger

_log = get_logger("Memory")


class MemoryManager:
    def __init__(self, total_memory: int) -> None:
        if total_memory < 0:
            raise ValueError("total_memory must be non-negative")
        self.total_memory = total_memory
        self.used_memory = 0
        self._by_pid: Dict[int, int] = {}

    def allocate(self, pid: int, amount: int) -> bool:
        if amount < 0:
            _log(f"allocate pid={pid}: reject negative amount={amount}")
            return False
        current = self._by_pid.get(pid, 0)
        delta = amount - current
        if self.used_memory + delta > self.total_memory:
            _log(
                f"allocate pid={pid}: reject need {delta} free "
                f"{self.total_memory - self.used_memory} (want total {amount})"
            )
            return False
        self.used_memory += delta
        self._by_pid[pid] = amount
        _log(f"allocate pid={pid}: set reservation to {amount} (used {self.used_memory}/{self.total_memory})")
        return True

    def free(self, pid: int) -> int:
        """Release all memory tracked for ``pid``. Returns bytes freed."""
        had = self._by_pid.pop(pid, 0)
        self.used_memory = max(0, self.used_memory - had)
        _log(f"free pid={pid}: released {had} (used {self.used_memory}/{self.total_memory})")
        return had
