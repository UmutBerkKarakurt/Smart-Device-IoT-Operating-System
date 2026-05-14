"""Phase 4: hierarchical FS, open file table, I/O blocking + wake, scheduler + file locks."""

from __future__ import annotations

from filesystem.file_system import FileSystem, normalize_path
from process.pcb import ProcessState
from process.process_manager import ProcessManager
from process.scheduler import Scheduler, create_scheduler
from os_core.simulation import Simulation


def test_normalize_and_paths() -> None:
    assert normalize_path("/logs//temperature.log") == "/logs/temperature.log"
    assert normalize_path("logs/temperature.log") == "/logs/temperature.log"


def test_mkdir_list_append() -> None:
    fs = FileSystem()
    assert fs.mkdir("/logs")
    assert fs.create("/logs/temperature.log")
    assert fs.append("/logs/temperature.log", "a", writer_pid=1)
    assert fs.append("/logs/temperature.log", "b", writer_pid=1)
    assert fs.read("/logs/temperature.log") == "ab"
    names = fs.list_dir("/logs")
    assert names == ["temperature.log"]


def test_open_close_fd_and_pcb() -> None:
    sim = Simulation()
    sim._reset_cpu_execution_state()
    sim.fs.mkdir("/data")
    sim.fs.create("/data/network_sync.txt")
    p = sim.processes.create_process("p", cpu_burst_time=10)
    sim.processes.change_state(p.pid, ProcessState.READY)
    fd = sim.open_file(p.pid, "/data/network_sync.txt", "r")
    assert fd is not None
    pcb = sim.processes.get_process(p.pid)
    assert pcb is not None
    assert (fd, "/data/network_sync.txt", "r") in pcb.opened_files
    assert sim.close_file(p.pid, fd) is True
    assert pcb.opened_files == []


def test_io_blocking_completion_and_scheduler_skips_blocked() -> None:
    sim = Simulation()
    sim._reset_cpu_execution_state()
    sim.scheduler = create_scheduler("fifo")
    sim.file_io_ticks = 2
    sim.fs.mkdir("/d")
    sim.fs.create("/d/f.txt")
    a = sim.processes.create_process("A", cpu_burst_time=5)
    b = sim.processes.create_process("B", cpu_burst_time=5)
    for p in (a, b):
        sim.processes.change_state(p.pid, ProcessState.READY)
        sim.scheduler.enqueue(p)

    assert sim.file_blocking_write(a.pid, "/d/f.txt", "x") == "io_started"
    assert sim.processes.get_process(a.pid).state == ProcessState.BLOCKED
    assert sim.pending_file_io_jobs() == 1

    assert sim._cpu_step() is True
    pcb_b = sim.processes.get_process(b.pid)
    assert pcb_b is not None
    assert pcb_b.program_counter >= 1
    assert sim.processes.get_process(a.pid).state == ProcessState.BLOCKED
    assert sim.pending_file_io_jobs() == 1

    assert sim._cpu_step() is True
    assert sim.pending_file_io_jobs() == 0
    assert sim.processes.get_process(a.pid).state == ProcessState.READY
    assert sim.fs.read("/d/f.txt") == "x"


def test_file_lock_contention_fifo() -> None:
    sim = Simulation()
    sim._reset_cpu_execution_state()
    sim.file_io_ticks = 2
    sim.fs.mkdir("/d")
    sim.fs.create("/d/x.txt")
    p1 = sim.processes.create_process("P1", cpu_burst_time=3)
    p2 = sim.processes.create_process("P2", cpu_burst_time=3)
    for p in (p1, p2):
        sim.processes.change_state(p.pid, ProcessState.READY)
        sim.scheduler.enqueue(p)

    assert sim.file_blocking_write(p1.pid, "/d/x.txt", "hold") == "io_started"
    assert sim.file_blocking_read(p2.pid, "/d/x.txt") == "lock_blocked"
    assert sim.processes.get_process(p2.pid).state == ProcessState.BLOCKED

    assert sim._cpu_step() is True
    assert sim.pending_file_io_jobs() == 1

    assert sim._cpu_step() is True
    assert sim.fs.read("/d/x.txt") == "hold"
    assert sim.processes.get_process(p2.pid).state == ProcessState.READY

    assert sim.file_blocking_read(p2.pid, "/d/x.txt") == "io_started"
    assert sim._cpu_step() is True
    assert sim._cpu_step() is True
    assert sim.pending_file_io_jobs() == 0


def test_scheduler_pick_skips_blocked_pid() -> None:
    pm = ProcessManager()
    sch = Scheduler()
    a = pm.create_process("a", cpu_burst_time=1)
    b = pm.create_process("b", cpu_burst_time=1)
    pm.change_state(a.pid, ProcessState.BLOCKED)
    pm.change_state(b.pid, ProcessState.READY)
    sch.enqueue(a)
    sch.enqueue(b)
    nxt = sch.pick_next(pm.pid_table())
    assert nxt is not None and nxt.pid == b.pid


def test_delete_after_close_and_metadata() -> None:
    fs = FileSystem(initial_tick=10)
    fs.set_simulation_tick(10)
    fs.mkdir("/logs")
    fs.create("/logs/camera.log", creator_pid=5)
    meta = fs.get_metadata("/logs/camera.log")
    assert meta is not None
    assert meta.created_at == 10 and meta.size == 0
    fs.write("/logs/camera.log", "ok", writer_pid=5)
    m2 = fs.get_metadata("/logs/camera.log")
    assert m2 is not None and m2.size == 2
