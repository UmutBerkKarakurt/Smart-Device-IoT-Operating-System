"""FIFO mutex with wait queue; integrates with simulator via ``SyncPorts``."""

from __future__ import annotations

from collections import deque
from typing import Deque, Optional

from concurrency.sync_ports import SyncPorts
from os_core.logger import get_logger

_log = get_logger("Concurrency")


class Lock:
    """One owner at a time; contending pids wait in FIFO order when ``SyncPorts`` is provided."""

    def __init__(self, name: str = "mutex") -> None:
        self.name = name
        self._owner: Optional[int] = None
        self._waiting_queue: Deque[int] = deque()

    @property
    def owner(self) -> Optional[int]:
        return self._owner

    @property
    def owner_pid(self) -> Optional[int]:
        return self._owner

    @property
    def waiting_queue(self) -> Deque[int]:
        return self._waiting_queue

    def acquire(self, pid: int, ports: Optional[SyncPorts] = None) -> bool:
        if self._owner is None:
            self._owner = pid
            _log(f"{self.name}: acquired by pid={pid}")
            return True
        if self._owner == pid:
            _log(f"{self.name}: pid={pid} already holds lock")
            return True
        if ports is None:
            _log(f"{self.name}: acquire failed for pid={pid} (held by {self._owner})")
            return False
        self._waiting_queue.append(pid)
        _log(
            f"{self.name}: contention pid={pid} blocked waiting for owner={self._owner} "
            f"queue={[p for p in self._waiting_queue]}"
        )
        if ports.log_scheduler:
            ports.log_scheduler(f"sync: pid={pid} waiting on mutex {self.name!r}")
        ports.block_process(pid)
        recomp = getattr(ports, "recompute_mutex_inheritance", None)
        if recomp is not None:
            recomp(self)
        return False

    def release(self, pid: int, ports: Optional[SyncPorts] = None) -> bool:
        if self._owner is None:
            _log(f"{self.name}: release ignored (not held)")
            return False
        if self._owner != pid:
            _log(f"{self.name}: release denied pid={pid} owner={self._owner}")
            return False
        self._owner = None
        _log(f"{self.name}: released by pid={pid}")
        clr = getattr(ports, "clear_mutex_inheritance_for_holder", None) if ports is not None else None
        if clr is not None:
            clr(pid)
        if ports is None or not self._waiting_queue:
            return True
        next_pid = self._waiting_queue.popleft()
        self._owner = next_pid
        _log(
            f"{self.name}: ownership transferred to pid={next_pid} "
            f"(remaining_waiters={[p for p in self._waiting_queue]})"
        )
        recomp = getattr(ports, "recompute_mutex_inheritance", None)
        if recomp is not None:
            recomp(self)
        if ports.log_scheduler:
            ports.log_scheduler(f"sync: mutex {self.name!r} granted to pid={next_pid}")
        ports.wake_process(next_pid)
        return True
