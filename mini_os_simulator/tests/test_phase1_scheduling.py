"""Phase 1: RR, metrics, and context switches (small synthetic cases)."""

from __future__ import annotations

from process.pcb import ProcessState
from process.process_manager import ProcessManager
from process.scheduler import Scheduler, create_scheduler
from os_core.simulation import Simulation


def _run_to_completion(sim: Simulation) -> None:
    sim._run_until_all_terminated()


def test_fifo_scheduler_still_fifo_order() -> None:
    pm = ProcessManager()
    sch = Scheduler()
    a = pm.create_process("a", cpu_burst_time=1)
    b = pm.create_process("b", cpu_burst_time=1)
    pm.change_state(a.pid, ProcessState.READY)
    pm.change_state(b.pid, ProcessState.READY)
    sch.enqueue(a)
    sch.enqueue(b)
    table = pm.pid_table()
    assert sch.pick_next(table).pid == a.pid
    assert sch.pick_next(table).pid == b.pid


def test_rr_requeues_unfinished_and_terminates() -> None:
    sim = Simulation(total_memory=4096)
    sim.scheduler = create_scheduler("rr", quantum=2)
    sim._reset_cpu_execution_state()
    p1 = sim.processes.create_process("P1", cpu_burst_time=4, arrival_time=0, memory_required=16)
    p2 = sim.processes.create_process("P2", cpu_burst_time=2, arrival_time=0, memory_required=16)
    assert sim.memory.allocate(p1.pid, 16)
    assert sim.memory.allocate(p2.pid, 16)
    sim.processes.change_state(p1.pid, ProcessState.READY)
    sim.processes.change_state(p2.pid, ProcessState.READY)
    sim.scheduler.enqueue(p1)
    sim.scheduler.enqueue(p2)
    _run_to_completion(sim)
    for p in (p1, p2):
        pcb = sim.processes.get_process(p.pid)
        assert pcb is not None
        assert pcb.state == ProcessState.TERMINATED
        assert pcb.remaining_time == 0


def test_metrics_single_process() -> None:
    sim = Simulation(total_memory=4096)
    sim.scheduler = create_scheduler("fifo")
    sim._reset_cpu_execution_state()
    p = sim.processes.create_process("solo", cpu_burst_time=3, arrival_time=0, memory_required=8)
    assert sim.memory.allocate(p.pid, 8)
    sim.processes.change_state(p.pid, ProcessState.READY)
    sim.scheduler.enqueue(p)
    _run_to_completion(sim)
    pcb = sim.processes.get_process(p.pid)
    assert pcb is not None
    assert pcb.turnaround_time == 3
    assert pcb.waiting_time == 0
    assert pcb.response_time == 0
    assert pcb.completion_time == 3


def test_context_switch_count_increments() -> None:
    sim = Simulation(total_memory=4096)
    sim.scheduler = create_scheduler("fifo")
    sim._reset_cpu_execution_state()
    a = sim.processes.create_process("A", cpu_burst_time=2, arrival_time=0, memory_required=8)
    b = sim.processes.create_process("B", cpu_burst_time=2, arrival_time=0, memory_required=8)
    assert sim.memory.allocate(a.pid, 8)
    assert sim.memory.allocate(b.pid, 8)
    sim.processes.change_state(a.pid, ProcessState.READY)
    sim.processes.change_state(b.pid, ProcessState.READY)
    sim.scheduler.enqueue(a)
    sim.scheduler.enqueue(b)
    _run_to_completion(sim)
    assert sim.context_switches >= 2
