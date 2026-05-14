"""Phase 5: priority scheduler, inheritance, aging, deterministic ordering."""

from __future__ import annotations

from concurrency.locks import Lock
from process.pcb import ProcessState
from process.process_manager import ProcessManager
from process.scheduler import PriorityScheduler, create_scheduler
from os_core.simulation import Simulation


def test_priority_scheduler_orders_ready_by_effective_priority() -> None:
    pm = ProcessManager()
    sch = create_scheduler("priority")
    assert isinstance(sch, PriorityScheduler)
    lo = pm.create_process("Low", priority=1, cpu_burst_time=10, arrival_time=0)
    hi = pm.create_process("High", priority=5, cpu_burst_time=10, arrival_time=0)
    pm.change_state(lo.pid, ProcessState.READY)
    pm.change_state(hi.pid, ProcessState.READY)
    sch.enqueue(lo)
    sch.enqueue(hi)
    table = pm.pid_table()
    assert sch.pick_next(table).pid == hi.pid
    assert sch.pick_next(table).pid == lo.pid


def test_priority_scheduler_tie_break_fifo_arrival_then_pid() -> None:
    pm = ProcessManager()
    sch = create_scheduler("priority")
    a = pm.create_process("A", priority=2, cpu_burst_time=1, arrival_time=0)
    b = pm.create_process("B", priority=2, cpu_burst_time=1, arrival_time=1)
    pm.change_state(a.pid, ProcessState.READY)
    pm.change_state(b.pid, ProcessState.READY)
    sch.enqueue(a)
    sch.enqueue(b)
    table = pm.pid_table()
    assert sch.pick_next(table).pid == a.pid
    assert sch.pick_next(table).pid == b.pid


def test_inheritance_raises_holder_effective_priority_when_high_waits() -> None:
    sim = Simulation(total_memory=4096)
    sim.mutex_inheritance_enabled = True
    sim._reset_cpu_execution_state()
    low = sim.processes.create_process("Low", priority=1, memory_required=16, cpu_burst_time=5, arrival_time=0)
    high = sim.processes.create_process("High", priority=4, memory_required=16, cpu_burst_time=5, arrival_time=0)
    sim.memory.allocate(low.pid, 16)
    sim.memory.allocate(high.pid, 16)
    lock = Lock("L")
    assert lock.acquire(low.pid, None) is True
    assert low.mutex_inheritance_floor == 0
    ports = sim._make_sync_ports()
    assert lock.acquire(high.pid, ports) is False
    assert low.mutex_inheritance_floor == 4
    assert low.effective_priority == 4


def test_restoration_on_release_clears_holder_floor() -> None:
    sim = Simulation(total_memory=4096)
    sim.mutex_inheritance_enabled = True
    sim._reset_cpu_execution_state()
    low = sim.processes.create_process("Low", priority=1, memory_required=16, cpu_burst_time=5, arrival_time=0)
    high = sim.processes.create_process("High", priority=4, memory_required=16, cpu_burst_time=5, arrival_time=0)
    sim.memory.allocate(low.pid, 16)
    sim.memory.allocate(high.pid, 16)
    lock = Lock("L")
    assert lock.acquire(low.pid, None) is True
    ports = sim._make_sync_ports()
    assert lock.acquire(high.pid, ports) is False
    assert low.mutex_inheritance_floor == 4
    assert lock.release(low.pid, ports) is True
    assert low.mutex_inheritance_floor == 0


def test_aging_increases_effective_priority_deterministically() -> None:
    sim = Simulation(total_memory=4096)
    sim.scheduler = create_scheduler("priority")
    sim.priority_aging_enabled = True
    sim.aging_interval = 3
    sim._reset_cpu_execution_state()
    p = sim.processes.create_process("Age", priority=1, memory_required=8, cpu_burst_time=20, arrival_time=0)
    sim.memory.allocate(p.pid, 8)
    sim.processes.change_state(p.pid, ProcessState.READY)
    sim.scheduler.enqueue(p)
    for _ in range(4):
        assert sim._cpu_step()
    assert p.aging_bonus == 1
    assert p.effective_priority == 2


def test_low_holder_makes_cpu_progress_sooner_with_inheritance_fixed_script() -> None:
    def run(*, inheritance: bool) -> int:
        s = Simulation(total_memory=4096)
        s.enable_memory_access = False
        s._reset_cpu_execution_state()
        s.mutex_inheritance_enabled = inheritance
        s.priority_aging_enabled = False
        s.scheduler = create_scheduler("priority")
        dl = s.processes.create_process(
            "DataLoggerProcess",
            priority=1,
            memory_required=64,
            cpu_burst_time=30,
            arrival_time=0,
        )
        net = s.processes.create_process(
            "NetworkSyncProcess",
            priority=2,
            memory_required=64,
            cpu_burst_time=30,
            arrival_time=0,
        )
        alarm = s.processes.create_process(
            "AlarmMonitorProcess",
            priority=3,
            memory_required=64,
            cpu_burst_time=30,
            arrival_time=0,
        )
        for pcb in (dl, net, alarm):
            s.memory.allocate(pcb.pid, 64)
        mtx = Lock("sensor_lock")
        assert mtx.acquire(dl.pid, None) is True
        for pcb in (dl, net, alarm):
            s.processes.change_state(pcb.pid, ProcessState.READY)
            s.scheduler.enqueue(pcb)
        ports = s._make_sync_ports()
        assert mtx.acquire(alarm.pid, ports) is False
        for _ in range(12):
            assert s._cpu_step()
        dl_p = s.processes.get_process(dl.pid)
        assert dl_p is not None
        return dl_p.remaining_time

    rem_without = run(inheritance=False)
    rem_with = run(inheritance=True)
    assert rem_with < rem_without
