"""Ready-queue schedulers: FIFO baseline and optional round-robin quantum."""

from __future__ import annotations

from collections import deque
from typing import Deque, List, Literal, Optional

from os_core.logger import get_logger
from process.pcb import PCB, ProcessState

_log = get_logger("Scheduler")

PolicyName = Literal["fifo", "rr"]


class Scheduler:
    def __init__(self) -> None:
        self._ready: Deque[int] = deque()

    def enqueue(self, pcb: PCB) -> None:
        if pcb.state not in (ProcessState.READY, ProcessState.NEW):
            _log(f"enqueue ignored pid={pcb.pid} (state={pcb.state.name})")
            return
        if pcb.pid in self._ready:
            _log(f"enqueue skip duplicate pid={pcb.pid}")
            return
        self._ready.append(pcb.pid)
        _log(f"Enqueued pid={pcb.pid} name={pcb.name!r}; queue={[p for p in self._ready]}")

    def pick_next(self, process_table: dict) -> Optional[PCB]:
        """Pop next READY pid from queue and return its PCB if still valid."""
        while self._ready:
            pid = self._ready.popleft()
            pcb = process_table.get(pid)
            if pcb is None:
                _log(f"pick_next: stale pid={pid} removed")
                continue
            if pcb.state == ProcessState.TERMINATED:
                _log(f"pick_next: skip terminated pid={pid}")
                continue
            _log(f"Picked next pid={pid} name={pcb.name!r}")
            return pcb
        _log("pick_next: ready queue empty")
        return None

    def ready_pids(self) -> List[int]:
        return list(self._ready)


class RoundRobinScheduler(Scheduler):
    """Same ready queue as FIFO; quantum is consumed by the CPU simulator (see Simulation)."""

    def __init__(self, quantum: int = 2) -> None:
        super().__init__()
        if quantum < 1:
            raise ValueError("quantum must be >= 1")
        self.quantum = quantum


def create_scheduler(policy: PolicyName, *, quantum: int = 2) -> Scheduler:
    """Factory: FIFO uses implicit quantum 1 in the simulator; RR carries an explicit quantum."""
    if policy == "fifo":
        return Scheduler()
    if policy == "rr":
        return RoundRobinScheduler(quantum=quantum)
    raise ValueError(f"unknown policy: {policy!r}")
