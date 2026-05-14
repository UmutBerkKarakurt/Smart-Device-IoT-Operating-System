"""Smoke tests for PCB, memory accounting, scheduler FIFO, and file system."""

from __future__ import annotations

from filesystem.file_system import FileSystem
from memory.memory_manager import MemoryManager
from process.pcb import PCB, ProcessState
from process.process_manager import ProcessManager
from process.scheduler import Scheduler


def test_pcb_remaining_time_defaults_from_burst() -> None:
    pcb = PCB(pid=1, name="t", cpu_burst_time=4)
    assert pcb.remaining_time == 4


def test_memory_reject_then_allocate() -> None:
    mm = MemoryManager(100)
    assert mm.allocate(1, 60) is True
    assert mm.allocate(2, 50) is False
    assert mm.allocate(2, 40) is True
    mm.free(1)
    assert mm.used_memory == 40


def test_fifo_order() -> None:
    pm = ProcessManager()
    sch = Scheduler()
    a = pm.create_process("a", cpu_burst_time=1)
    b = pm.create_process("b", cpu_burst_time=1)
    pm.change_state(a.pid, ProcessState.READY)
    pm.change_state(b.pid, ProcessState.READY)
    sch.enqueue(a)
    sch.enqueue(b)
    table = pm.pid_table()
    first = sch.pick_next(table)
    second = sch.pick_next(table)
    assert first is not None and second is not None
    assert first.pid == a.pid
    assert second.pid == b.pid


def test_file_roundtrip() -> None:
    fs = FileSystem()
    assert fs.create("/x.txt") is True
    assert fs.write("/x.txt", "hello") is True
    assert fs.read("/x.txt") == "hello"
    assert fs.delete("/x.txt") is True
    assert fs.read("/x.txt") is None
