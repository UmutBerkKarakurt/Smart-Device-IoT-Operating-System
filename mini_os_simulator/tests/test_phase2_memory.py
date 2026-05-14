"""Phase 2: paging, translation, faults, scheduler interaction, FIFO eviction."""

from __future__ import annotations

from process.pcb import ProcessState
from process.process_manager import ProcessManager
from process.scheduler import Scheduler
from memory.memory_manager import MemoryManager, PageFaultError, deterministic_logical_address
from os_core.simulation import Simulation


def test_translate_address_hit() -> None:
    mm = MemoryManager(4096, page_size=256)
    assert mm.allocate(1, 512) is True
    page_table = {0: 2}
    phys, page, off, frame = mm.translate_address(1, 10, page_table)
    assert page == 0 and off == 10 and frame == 2
    assert phys == 2 * 256 + 10


def test_translate_raises_fault_when_not_resident() -> None:
    mm = MemoryManager(4096, page_size=256)
    assert mm.allocate(1, 512) is True
    try:
        mm.translate_address(1, 0, {})
        assert False, "expected PageFaultError"
    except PageFaultError as e:
        assert e.pid == 1 and e.logical_page == 0


def test_deterministic_logical_address_stable() -> None:
    a = deterministic_logical_address(3, 100, 64, 4)
    b = deterministic_logical_address(3, 100, 64, 4)
    assert a == b


def test_fault_blocked_then_ready_after_fault_ticks() -> None:
    mm = MemoryManager(1024, page_size=256)
    mm.fault_handling_ticks = 2
    pm = ProcessManager()
    sch = Scheduler()
    cleared: list[tuple[int, int]] = []
    installed: list[tuple[int, int, int]] = []
    readied: list[int] = []

    def clear_m(pid: int, lp: int) -> None:
        cleared.append((pid, lp))

    def install_m(pid: int, lp: int, fr: int) -> None:
        installed.append((pid, lp, fr))
        pcb = pm.get_process(pid)
        if pcb is not None:
            pcb.page_table[lp] = fr

    def ready_m(pid: int) -> None:
        readied.append(pid)
        pcb = pm.get_process(pid)
        if pcb is not None and pcb.state == ProcessState.BLOCKED:
            pm.change_state(pid, ProcessState.READY)
            sch.enqueue(pcb)

    mm.ports.clear_page_mapping = clear_m
    mm.ports.install_page_mapping = install_m
    mm.ports.mark_ready_and_enqueue = ready_m

    p = pm.create_process("IoTTest", cpu_burst_time=5, memory_required=512)
    assert mm.allocate(p.pid, p.memory_required) is True

    pm.change_state(p.pid, ProcessState.BLOCKED)
    mm.begin_fault_resolution(p.pid, 0)
    assert mm.pending_fault_jobs() == 1

    mm.advance_fault_timers()
    assert mm.pending_fault_jobs() == 1
    mm.advance_fault_timers()
    assert mm.pending_fault_jobs() == 0
    assert installed and installed[0][0] == p.pid and installed[0][1] == 0
    assert readied == [p.pid]
    pcb = pm.get_process(p.pid)
    assert pcb is not None
    assert pcb.state == ProcessState.READY
    assert 0 in pcb.page_table


def test_scheduler_not_stuck_when_one_process_blocked() -> None:
    sim = Simulation(total_memory=2048, enable_memory_access=True)
    sim.memory.fault_handling_ticks = 1
    sim.scheduler = Scheduler()
    sim._reset_cpu_execution_state()
    a = sim.processes.create_process("CameraA", cpu_burst_time=4, memory_required=512)
    b = sim.processes.create_process("SensorB", cpu_burst_time=2, memory_required=256)
    assert sim.memory.allocate(a.pid, a.memory_required)
    assert sim.memory.allocate(b.pid, b.memory_required)
    sim.processes.change_state(a.pid, ProcessState.READY)
    sim.processes.change_state(b.pid, ProcessState.READY)
    sim.scheduler.enqueue(a)
    sim.scheduler.enqueue(b)

    steps = 0
    saw_blocked = False
    while steps < 5000 and any(
        p.state != ProcessState.TERMINATED for p in sim.processes.list_processes()
    ):
        sim._cpu_step()
        steps += 1
        if sim.processes.get_process(a.pid) and sim.processes.get_process(a.pid).state == ProcessState.BLOCKED:
            saw_blocked = True

    assert saw_blocked, "expected at least one BLOCKED state from paging"
    assert all(p.state == ProcessState.TERMINATED for p in sim.processes.list_processes())


def test_fifo_eviction_when_no_free_frame() -> None:
    mm = MemoryManager(640, page_size=256)
    assert mm.total_frames == 2
    pm = ProcessManager()

    def install(pid: int, lp: int, fr: int) -> None:
        pcb = pm.get_process(pid)
        if pcb is not None:
            pcb.page_table[lp] = fr

    def clear(pid: int, lp: int) -> None:
        pcb = pm.get_process(pid)
        if pcb is not None:
            pcb.page_table.pop(lp, None)

    mm.ports.install_page_mapping = install
    mm.ports.clear_page_mapping = clear
    mm.ports.mark_ready_and_enqueue = lambda _pid: None

    p1 = pm.create_process("P1", memory_required=512)
    p2 = pm.create_process("P2", memory_required=128)
    assert mm.allocate(p1.pid, 512)
    assert mm.allocate(p2.pid, 128)

    mm.fault_handling_ticks = 1
    mm.begin_fault_resolution(p1.pid, 0)
    mm.advance_fault_timers()
    mm.begin_fault_resolution(p1.pid, 1)
    mm.advance_fault_timers()
    assert len(mm._free_frames) == 0

    mm.begin_fault_resolution(p2.pid, 0)
    mm.advance_fault_timers()
    assert p2.page_table.get(0) is not None
    assert 0 not in p1.page_table
    snap = mm.frame_table_snapshot()
    assert sum(1 for x in snap if x is not None) == 2
