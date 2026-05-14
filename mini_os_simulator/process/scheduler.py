"""Ready-queue schedulers: FIFO baseline, optional round-robin quantum, and Phase 5 priority."""

from __future__ import annotations

from collections import deque
from typing import Deque, List, Literal, Optional

from os_core.logger import get_logger
from process.pcb import PCB, ProcessState

_log = get_logger("Scheduler")

PolicyName = Literal["fifo", "rr", "priority"]


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
            if pcb.state == ProcessState.BLOCKED:
                _log(f"pick_next: skip blocked pid={pid}")
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


class PriorityScheduler(Scheduler):
    """
    Among READY processes currently in the ready queue, pick the highest ``effective_priority``.
    Tie-break (documented): lower ``arrival_time`` first (FIFO among equals), then lower ``pid``.
    BLOCKED / TERMINATED / missing PCBs are skipped and not re-queued here.
    """

    def pick_next(self, process_table: dict) -> Optional[PCB]:
        staged: List[tuple[int, PCB]] = []
        order = 0
        while self._ready:
            pid = self._ready.popleft()
            pcb = process_table.get(pid)
            if pcb is None:
                _log(f"pick_next: stale pid={pid} removed")
                continue
            if pcb.state == ProcessState.TERMINATED:
                _log(f"pick_next: skip terminated pid={pid}")
                continue
            if pcb.state == ProcessState.BLOCKED:
                _log(f"pick_next: skip blocked pid={pid}")
                continue
            staged.append((order, pcb))
            order += 1

        if not staged:
            _log("pick_next: ready queue empty")
            return None

        def sort_key(t: tuple[int, PCB]) -> tuple[int, int, int, int]:
            o, p = t
            # Higher effective_priority first; then earlier arrival; then lower pid; then queue order.
            return (-p.effective_priority, p.arrival_time, p.pid, o)

        staged.sort(key=sort_key)
        chosen = staged[0][1]
        _log(f"PriorityScheduler selected pid={chosen.pid} priority={chosen.effective_priority}")

        for o, pcb in staged:
            if pcb.pid != chosen.pid:
                self._ready.append(pcb.pid)
        return chosen


def create_scheduler(policy: PolicyName, *, quantum: int = 2) -> Scheduler:
    """Factory: FIFO uses implicit quantum 1 in the simulator; RR carries an explicit quantum."""
    if policy == "fifo":
        return Scheduler()
    if policy == "rr":
        return RoundRobinScheduler(quantum=quantum)
    if policy == "priority":
        return PriorityScheduler()
    raise ValueError(f"unknown policy: {policy!r}")
