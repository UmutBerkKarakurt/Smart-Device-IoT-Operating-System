"""Lifecycle and registry for simulated processes."""

from __future__ import annotations

from typing import Dict, List, Optional

from os_core.logger import get_logger
from process.pcb import PCB, ProcessState

_log = get_logger("Process")


class ProcessManager:
    def __init__(self) -> None:
        self._processes: Dict[int, PCB] = {}
        self._next_pid: int = 1

    def pid_table(self) -> Dict[int, PCB]:
        """Mutable map used by the scheduler to resolve ready-queue ids."""
        return self._processes

    def create_process(
        self,
        name: str,
        *,
        priority: int = 0,
        memory_required: int = 0,
        cpu_burst_time: int = 0,
        arrival_time: int = 0,
        opened_files: Optional[List[str]] = None,
    ) -> PCB:
        pid = self._next_pid
        self._next_pid += 1
        pcb = PCB(
            pid=pid,
            name=name,
            state=ProcessState.NEW,
            priority=priority,
            memory_required=memory_required,
            cpu_burst_time=cpu_burst_time,
            arrival_time=arrival_time,
            opened_files=list(opened_files or []),
        )
        self._processes[pid] = pcb
        _log(f"Created process pid={pid} name={name!r} state={pcb.state.name}")
        return pcb

    def get_process(self, pid: int) -> Optional[PCB]:
        return self._processes.get(pid)

    def list_processes(self) -> List[PCB]:
        return list(self._processes.values())

    def change_state(self, pid: int, new_state: ProcessState) -> bool:
        pcb = self._processes.get(pid)
        if pcb is None:
            _log(f"change_state: unknown pid={pid}")
            return False
        old = pcb.state
        pcb.state = new_state
        _log(f"pid={pid} state {old.name} -> {new_state.name}")
        return True

    def terminate(self, pid: int) -> bool:
        pcb = self._processes.get(pid)
        if pcb is None:
            _log(f"terminate: unknown pid={pid}")
            return False
        pcb.state = ProcessState.TERMINATED
        _log(f"Terminated pid={pid} name={pcb.name!r}")
        return True
