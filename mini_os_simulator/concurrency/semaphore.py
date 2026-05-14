"""Counting semaphore with FIFO wait queue; deterministic ``wait`` / ``signal``."""

from __future__ import annotations

from collections import deque
from typing import Deque, Set

from concurrency.sync_ports import SyncPorts
from os_core.logger import get_logger

_log = get_logger("Concurrency")


class CountingSemaphore:
    """
    Counting semaphore with explicit ``wait`` / ``signal``.

    ``wait`` decrements ``count`` when a permit is available. If ``count`` is zero and there
    is no pending handoff for this ``pid``, the caller is FIFO-queued and blocked.

    ``signal``: if waiters exist, one waiter receives a **direct handoff** (recorded in
    ``_pending_grants``) and is woken — ``count`` is unchanged, matching the classic
    ``V`` that releases a blocked ``P``. If no waiters, ``count`` is incremented.
    A woken process completes its next ``wait`` by consuming the pending grant (no extra
    decrement from ``count``).
    """

    def __init__(self, name: str, initial: int) -> None:
        self.name = name
        self._count = int(initial)
        self._waiting_queue: Deque[int] = deque()
        self._pending_grants: Set[int] = set()

    @property
    def count(self) -> int:
        return self._count

    @property
    def waiting_queue(self) -> Deque[int]:
        return self._waiting_queue

    def wait(self, pid: int, ports: SyncPorts) -> bool:
        """Return True if a permit was taken (or handoff consumed); False if blocked."""
        if pid in self._pending_grants:
            self._pending_grants.remove(pid)
            _log(f"{self.name}: wait resumed pid={pid} (post-signal handoff) count={self._count}")
            return True
        if self._count > 0:
            self._count -= 1
            _log(f"{self.name}: wait ok pid={pid} count_after={self._count}")
            return True
        self._waiting_queue.append(pid)
        _log(
            f"{self.name}: wait blocked pid={pid} count=0 queue={[p for p in self._waiting_queue]}"
        )
        if ports.log_scheduler:
            ports.log_scheduler(f"sync: pid={pid} blocked on semaphore {self.name!r}")
        ports.block_process(pid)
        if ports.on_semaphore_blocked is not None:
            ports.on_semaphore_blocked(self.name, pid)
        return False

    def signal(self, pid: int, ports: SyncPorts) -> None:
        """Release one permit to a FIFO waiter (handoff) or bump ``count`` when idle."""
        if self._waiting_queue:
            wakee = self._waiting_queue.popleft()
            self._pending_grants.add(wakee)
            _log(
                f"{self.name}: signal by pid={pid} handoff to pid={wakee} "
                f"(remaining_waiters={[p for p in self._waiting_queue]} count={self._count})"
            )
            if ports.log_scheduler:
                ports.log_scheduler(f"sync: semaphore {self.name!r} signal -> wake pid={wakee}")
            ports.wake_process(wakee)
            if ports.on_semaphore_signal_handoff is not None:
                ports.on_semaphore_signal_handoff(self.name, pid, wakee)
            return
        self._count += 1
        _log(f"{self.name}: signal by pid={pid} count_after={self._count}")
