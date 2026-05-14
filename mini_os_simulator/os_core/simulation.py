"""Wires core subsystems and runs Phase 0–5 demos (optional paged memory access)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional

from os_core.logger import get_logger
from concurrency.locks import Lock
from concurrency.semaphore import CountingSemaphore
from concurrency.shared_buffer import SharedBuffer
from concurrency.sync_ports import SyncPorts
from filesystem.file_system import FileSystem, normalize_path
from filesystem.file_system_ports import FileSystemPorts
from memory.memory_manager import MemoryManager, deterministic_logical_address
from process.pcb import PCB, ProcessState
from process.process_manager import ProcessManager
from process.scheduler import PriorityScheduler, Scheduler, create_scheduler

_log = get_logger("Simulation")
_sch = get_logger("Scheduler")
_proc = get_logger("Process")
_conc = get_logger("Concurrency")


@dataclass
class PolicyRunStats:
    policy_label: str
    avg_waiting: float
    avg_turnaround: float
    avg_response: float
    context_switches: int


@dataclass
class Phase5StressMetrics:
    """Deterministic counters for Phase 5 stress comparison (inheritance on vs off)."""

    label: str
    context_switches: int
    sim_clock: int
    alarm_first_dispatch_tick: Optional[int]
    data_logger_first_dispatch_tick: Optional[int]
    network_first_dispatch_tick: Optional[int]
    data_logger_remaining_before_release: int
    network_remaining_before_release: int


@dataclass
class OpenFileDescriptor:
    fd: int
    pid: int
    path: str
    mode: str
    offset: int


@dataclass
class PendingFileIoJob:
    ticks_remaining: int
    pid: int
    path: str
    op: str
    payload: Optional[str] = None


class Simulation:
    """Coordinates process table, scheduler, memory accounting, and FS."""

    def __init__(self, total_memory: int = 4096, *, enable_memory_access: bool = False) -> None:
        self.processes = ProcessManager()
        self.scheduler = Scheduler()
        self.memory = MemoryManager(total_memory)
        self.fs = FileSystem()
        self.enable_memory_access = enable_memory_access
        self._sim_clock: int = 0
        self._current_pcb: Optional[PCB] = None
        self._ticks_in_slice: int = 0
        self._cs_prev_pid: Optional[int] = None
        self.context_switches: int = 0
        self._next_fd: int = 1
        self._open_by_fd: Dict[int, OpenFileDescriptor] = {}
        self._pending_file_io: List[PendingFileIoJob] = []
        self.file_io_ticks: int = 3
        # Phase 5: priority aging (only applied when using PriorityScheduler; see _maybe_apply_aging).
        self.priority_aging_enabled: bool = True
        self.aging_interval: int = 5
        # When True, SyncPorts wires mutex inheritance callbacks into ``Lock`` operations.
        self.mutex_inheritance_enabled: bool = False
        self._wire_memory_ports()

    def _wire_memory_ports(self) -> None:
        self.memory.ports.clear_page_mapping = self._memory_clear_mapping
        self.memory.ports.install_page_mapping = self._memory_install_mapping
        self.memory.ports.mark_ready_and_enqueue = self._memory_ready_after_fault

    def _memory_clear_mapping(self, pid: int, logical_page: int) -> None:
        pcb = self.processes.get_process(pid)
        if pcb is not None:
            pcb.page_table.pop(logical_page, None)

    def _memory_install_mapping(self, pid: int, logical_page: int, frame: int) -> None:
        pcb = self.processes.get_process(pid)
        if pcb is None:
            return
        pcb.page_table[logical_page] = frame

    def _memory_ready_after_fault(self, pid: int) -> None:
        pcb = self.processes.get_process(pid)
        if pcb is None or pcb.state == ProcessState.TERMINATED:
            return
        if pcb.state == ProcessState.BLOCKED:
            self.processes.change_state(pid, ProcessState.READY)
            self.scheduler.enqueue(pcb)
            _sch(f"pid={pid} page-in complete -> READY, re-enqueued")

    def _sync_block_process(self, pid: int) -> None:
        """RUNNING/READY -> BLOCKED for sync waits; clear current CPU if it matches ``pid``."""
        pcb = self.processes.get_process(pid)
        if pcb is None or pcb.state == ProcessState.TERMINATED:
            return
        if pcb.state in (ProcessState.RUNNING, ProcessState.READY):
            self.processes.change_state(pid, ProcessState.BLOCKED)
        if self._current_pcb is not None and self._current_pcb.pid == pid:
            self._current_pcb = None
            self._ticks_in_slice = 0
            self._cs_prev_pid = None

    def _sync_wake_process(self, pid: int) -> None:
        """BLOCKED/READY -> READY and enqueue (idempotent enqueue for READY)."""
        pcb = self.processes.get_process(pid)
        if pcb is None or pcb.state == ProcessState.TERMINATED:
            return
        if pcb.state == ProcessState.BLOCKED:
            self.processes.change_state(pid, ProcessState.READY)
        if pcb.state == ProcessState.READY:
            self.scheduler.enqueue(pcb)

    def _recompute_mutex_inheritance(self, lock: Lock) -> None:
        """Set holder's ``mutex_inheritance_floor`` from blocked waiters' base+aging (no recursion)."""
        owner_pid = lock.owner_pid
        if owner_pid is None:
            return
        owner = self.processes.get_process(owner_pid)
        if owner is None:
            return
        prev = owner.mutex_inheritance_floor
        if not lock.waiting_queue:
            owner.mutex_inheritance_floor = 0
        else:
            mx = 0
            for wpid in lock.waiting_queue:
                w = self.processes.get_process(wpid)
                if w is not None:
                    mx = max(mx, w.priority + w.aging_bonus)
            owner.mutex_inheritance_floor = mx
        if owner.mutex_inheritance_floor > prev:
            _conc(
                f"{lock.name}: priority inheritance -> holder pid={owner_pid} "
                f"inherits floor={owner.mutex_inheritance_floor} (was {prev})"
            )
        elif prev > 0 and owner.mutex_inheritance_floor == 0:
            _conc(
                f"{lock.name}: priority inheritance cleared for holder pid={owner_pid} "
                f"(floor was {prev})"
            )

    def _clear_mutex_inheritance_for_holder(self, releaser_pid: int) -> None:
        pcb = self.processes.get_process(releaser_pid)
        if pcb is None:
            return
        was = pcb.mutex_inheritance_floor
        pcb.mutex_inheritance_floor = 0
        if was:
            _conc(
                f"mutex inheritance restored base scheduling weight for pid={releaser_pid} "
                f"(cleared inheritance floor was {was})"
            )

    def _maybe_apply_aging(self) -> None:
        if not self.priority_aging_enabled:
            return
        if not isinstance(self.scheduler, PriorityScheduler):
            return
        if self.aging_interval < 1:
            return
        if self._sim_clock == 0 or (self._sim_clock % self.aging_interval) != 0:
            return
        for pcb in self.processes.list_processes():
            if pcb.state != ProcessState.READY:
                continue
            pcb.aging_bonus += 1
            _sch(f"Aging applied pid={pcb.pid} effective_priority={pcb.effective_priority}")

    def _advance_sim_clock(self) -> None:
        self._sim_clock += 1

    def _make_sync_ports(self) -> SyncPorts:
        def log_s(msg: str) -> None:
            _sch(msg)

        if self.mutex_inheritance_enabled:
            return SyncPorts(
                block_process=self._sync_block_process,
                wake_process=self._sync_wake_process,
                log_scheduler=log_s,
                recompute_mutex_inheritance=self._recompute_mutex_inheritance,
                clear_mutex_inheritance_for_holder=self._clear_mutex_inheritance_for_holder,
            )
        return SyncPorts(
            block_process=self._sync_block_process,
            wake_process=self._sync_wake_process,
            log_scheduler=log_s,
        )

    def _make_fs_ports(self) -> FileSystemPorts:
        def log_s(msg: str) -> None:
            _sch(msg)

        return FileSystemPorts(
            block_process=self._sync_block_process,
            wake_process=self._sync_wake_process,
            log_scheduler=log_s,
        )

    def _advance_file_io_timers(self) -> None:
        if not self._pending_file_io:
            return
        still: List[PendingFileIoJob] = []
        for job in self._pending_file_io:
            job.ticks_remaining -= 1
            if job.ticks_remaining <= 0:
                self._complete_file_io(job)
            else:
                still.append(job)
        self._pending_file_io = still

    def _complete_file_io(self, job: PendingFileIoJob) -> None:
        ports = self._make_fs_ports()
        _sch(f"pid={job.pid} I/O complete op={job.op} path={job.path!r}")
        if job.op == "write":
            self.fs.write(job.path, job.payload or "", writer_pid=job.pid)
        elif job.op == "append":
            self.fs.append(job.path, job.payload or "", writer_pid=job.pid)
        elif job.op == "delete":
            self.fs.delete(job.path)
        # "read" — no content mutation; latency only
        self.fs.release_exclusive_lock(job.pid, job.path, ports)
        self._sync_wake_process(job.pid)
        _sch(f"pid={job.pid} wake after file I/O -> READY, re-enqueued")

    def pending_file_io_jobs(self) -> int:
        return len(self._pending_file_io)

    def open_file(self, pid: int, path: str, mode: str) -> Optional[int]:
        """Allocate fd, update PCB ``opened_files`` and inode ``open_count`` (no I/O latency)."""
        self.fs.set_simulation_tick(self._sim_clock)
        p = normalize_path(path)
        pcb = self.processes.get_process(pid)
        if pcb is None:
            _proc(f"open: unknown pid={pid}")
            return None
        if not self.fs.file_exists(p):
            _proc(f"pid={pid} open {p!r}: no such file")
            return None
        fd = self._next_fd
        self._next_fd += 1
        self._open_by_fd[fd] = OpenFileDescriptor(fd=fd, pid=pid, path=p, mode=mode, offset=0)
        self.fs.note_open(p)
        pcb.opened_files.append((fd, p, mode))
        _proc(f"pid={pid} open fd={fd} path={p!r} mode={mode!r}")
        return fd

    def close_file(self, pid: int, fd: int) -> bool:
        self.fs.set_simulation_tick(self._sim_clock)
        desc = self._open_by_fd.get(fd)
        pcb = self.processes.get_process(pid)
        if desc is None or pcb is None or desc.pid != pid:
            _proc(f"pid={pid} close fd={fd}: invalid")
            return False
        self.fs.note_close(desc.path)
        del self._open_by_fd[fd]
        pcb.opened_files = [e for e in pcb.opened_files if e[0] != fd]
        _proc(f"pid={pid} close fd={fd} path={desc.path!r}")
        return True

    def file_blocking_write(
        self, pid: int, path: str, content: str, *, io_ticks: Optional[int] = None
    ) -> Literal["io_started", "lock_blocked", "missing"]:
        self.fs.set_simulation_tick(self._sim_clock)
        p = normalize_path(path)
        if not self.fs.file_exists(p):
            return "missing"
        ticks = self.file_io_ticks if io_ticks is None else io_ticks
        ports = self._make_fs_ports()
        if not self.fs.try_acquire_exclusive_lock(pid, p, ports):
            return "lock_blocked"
        self._pending_file_io.append(
            PendingFileIoJob(ticks_remaining=ticks, pid=pid, path=p, op="write", payload=content)
        )
        _log(f"I/O queued pid={pid} op=write path={p!r} io_ticks={ticks}")
        _sch(f"pid={pid} blocked for I/O ({ticks} ticks) op=write path={p!r}")
        self._sync_block_process(pid)
        return "io_started"

    def file_blocking_append(
        self, pid: int, path: str, fragment: str, *, io_ticks: Optional[int] = None
    ) -> Literal["io_started", "lock_blocked", "missing"]:
        self.fs.set_simulation_tick(self._sim_clock)
        p = normalize_path(path)
        if not self.fs.file_exists(p):
            return "missing"
        ticks = self.file_io_ticks if io_ticks is None else io_ticks
        ports = self._make_fs_ports()
        if not self.fs.try_acquire_exclusive_lock(pid, p, ports):
            return "lock_blocked"
        self._pending_file_io.append(
            PendingFileIoJob(ticks_remaining=ticks, pid=pid, path=p, op="append", payload=fragment)
        )
        _log(f"I/O queued pid={pid} op=append path={p!r} io_ticks={ticks}")
        _sch(f"pid={pid} blocked for I/O ({ticks} ticks) op=append path={p!r}")
        self._sync_block_process(pid)
        return "io_started"

    def file_blocking_read(
        self, pid: int, path: str, *, io_ticks: Optional[int] = None
    ) -> Literal["io_started", "lock_blocked", "missing"]:
        self.fs.set_simulation_tick(self._sim_clock)
        p = normalize_path(path)
        if not self.fs.file_exists(p):
            return "missing"
        ticks = self.file_io_ticks if io_ticks is None else io_ticks
        ports = self._make_fs_ports()
        if not self.fs.try_acquire_exclusive_lock(pid, p, ports):
            return "lock_blocked"
        self._pending_file_io.append(
            PendingFileIoJob(ticks_remaining=ticks, pid=pid, path=p, op="read", payload=None)
        )
        _log(f"I/O queued pid={pid} op=read path={p!r} io_ticks={ticks}")
        _sch(f"pid={pid} blocked for I/O ({ticks} ticks) op=read path={p!r}")
        self._sync_block_process(pid)
        return "io_started"

    def file_blocking_delete(
        self, pid: int, path: str, *, io_ticks: Optional[int] = None
    ) -> Literal["io_started", "lock_blocked", "missing"]:
        self.fs.set_simulation_tick(self._sim_clock)
        p = normalize_path(path)
        if not self.fs.file_exists(p):
            return "missing"
        ticks = self.file_io_ticks if io_ticks is None else io_ticks
        ports = self._make_fs_ports()
        if not self.fs.try_acquire_exclusive_lock(pid, p, ports):
            return "lock_blocked"
        self._pending_file_io.append(
            PendingFileIoJob(ticks_remaining=ticks, pid=pid, path=p, op="delete", payload=None)
        )
        _log(f"I/O queued pid={pid} op=delete path={p!r} io_ticks={ticks}")
        _sch(f"pid={pid} blocked for I/O ({ticks} ticks) op=delete path={p!r}")
        self._sync_block_process(pid)
        return "io_started"

    def _reset_cpu_execution_state(self) -> None:
        self._sim_clock = 0
        self._current_pcb = None
        self._ticks_in_slice = 0
        self._cs_prev_pid = None
        self.context_switches = 0
        self._pending_file_io.clear()
        self._open_by_fd.clear()
        self._next_fd = 1

    def _phase4_cpu_ticks(self, ticks: int) -> None:
        """Advance simulation (CPU + fault timers + file I/O timers) for a fixed tick budget."""
        for _ in range(ticks):
            if not self._cpu_step():
                if self.memory.pending_fault_jobs() > 0 or self._pending_file_io:
                    continue
                break

    def _effective_quantum(self) -> int:
        return int(getattr(self.scheduler, "quantum", 1))

    def _note_context_switch_if_needed(self, pid: int, name: str) -> None:
        if self._cs_prev_pid != pid:
            self.context_switches += 1
            _sch(f"Context switch: pid={pid} {name} RUNNING")
        self._cs_prev_pid = pid

    def _cpu_step(self) -> bool:
        """One simulation tick: advance page-in timers, file I/O timers, then run CPU."""
        self._maybe_apply_aging()
        self.memory.advance_fault_timers()
        self._advance_file_io_timers()
        self.fs.set_simulation_tick(self._sim_clock)

        quantum = self._effective_quantum()
        table = self.processes.pid_table()

        while True:
            if self._current_pcb is None:
                pcb = self.scheduler.pick_next(table)
                if pcb is None:
                    if self.memory.pending_fault_jobs() > 0 or self._pending_file_io:
                        self._advance_sim_clock()
                        return True
                    return False
                self._current_pcb = pcb
                self._ticks_in_slice = 0

            pcb = self._current_pcb
            if self._ticks_in_slice == 0:
                self._note_context_switch_if_needed(pcb.pid, pcb.name)
                pcb.on_first_cpu_dispatch(self._sim_clock)
                self.processes.change_state(pcb.pid, ProcessState.RUNNING)

            if self.enable_memory_access and self.memory.logical_page_count_for(pcb.pid) > 0:
                lp_count = self.memory.logical_page_count_for(pcb.pid)
                la = deterministic_logical_address(
                    pcb.pid,
                    self._sim_clock,
                    self.memory.page_size,
                    lp_count,
                )
                acc = self.memory.try_access(pcb.pid, la, pcb.page_table)
                if acc.kind == "fault":
                    _sch(f"pid={pcb.pid} BLOCKED on page fault (page={acc.logical_page})")
                    self.processes.change_state(pcb.pid, ProcessState.BLOCKED)
                    self.memory.begin_fault_resolution(pcb.pid, acc.logical_page)
                    self._current_pcb = None
                    self._ticks_in_slice = 0
                    self._cs_prev_pid = None
                    continue

            if pcb.remaining_time > 0:
                pcb.remaining_time -= 1
                pcb.program_counter += 1
            self._ticks_in_slice += 1
            self._advance_sim_clock()

            executed_ticks = self._ticks_in_slice
            rem = pcb.remaining_time

            if rem == 0:
                _sch(f"pid={pcb.pid} executed for {executed_ticks} ticks, remaining_time=0")
                pcb.finalize_metrics(self._sim_clock)
                _sch(f"pid={pcb.pid} completed and terminated")
                self.processes.change_state(pcb.pid, ProcessState.TERMINATED)
                self.memory.free(pcb.pid)
                self._current_pcb = None
                self._ticks_in_slice = 0
                self._cs_prev_pid = None
                return True

            if self._ticks_in_slice >= quantum:
                _sch(f"pid={pcb.pid} executed for {executed_ticks} ticks, remaining_time={rem}")
                _sch(f"pid={pcb.pid} quantum expired, re-enqueued")
                self.processes.change_state(pcb.pid, ProcessState.READY)
                self.scheduler.enqueue(pcb)
                self._current_pcb = None
                self._ticks_in_slice = 0
            return True

    def _dispatch_one_tick(self) -> bool:
        """One scheduling tick (used by Phase 0)."""
        return self._cpu_step()

    def _run_fifo_partial_ticks(self, ticks: int) -> None:
        for _ in range(ticks):
            if not self._dispatch_one_tick():
                break

    def _run_fifo_until_idle(self) -> None:
        for _ in range(10_000):
            if not self._dispatch_one_tick():
                break

    def run_phase0_demo(self) -> None:
        """Narrative: processes → RAM → READY/FIFO ticks → FS → terminate + free → drain."""
        self.enable_memory_access = False
        self._reset_cpu_execution_state()
        _log("=== Phase 0 demo: FIFO + memory + FS + lock (skeleton) ===")

        p1 = self.processes.create_process(
            "init",
            priority=0,
            memory_required=512,
            cpu_burst_time=2,
            arrival_time=self._sim_clock,
        )
        p2 = self.processes.create_process(
            "worker",
            priority=1,
            memory_required=1024,
            cpu_burst_time=2,
            arrival_time=self._sim_clock,
        )
        p3 = self.processes.create_process(
            "logger",
            priority=0,
            memory_required=256,
            cpu_burst_time=50,
            arrival_time=self._sim_clock,
        )

        for p in (p1, p2, p3):
            if not self.memory.allocate(p.pid, p.memory_required):
                _log(f"demo: memory allocation failed for pid={p.pid}")

        for p in (p1, p2, p3):
            self.processes.change_state(p.pid, ProcessState.READY)
            self.scheduler.enqueue(p)

        lock = Lock(name="demo_resource")
        lock.acquire(p1.pid, None)
        lock.acquire(p2.pid, None)
        lock.release(p1.pid, None)
        lock.acquire(p2.pid, None)
        lock.release(p2.pid, None)

        self._run_fifo_partial_ticks(5)

        self.fs.create("notes.txt")
        self.fs.write("notes.txt", "Phase0: modular mini-OS simulator.")
        text = self.fs.read("notes.txt")
        if text is not None:
            _log(f"FS read-back preview: {text[:48]!r}")

        victim = p3.pid
        self.processes.terminate(victim)
        self.memory.free(victim)

        self._run_fifo_until_idle()

        _log("=== Phase 0 demo end ===")

    def _create_phase1_iot_processes(self) -> List[PCB]:
        """Same IoT-themed workload for FIFO and RR comparisons (priority ignored for scheduling)."""
        clock = self._sim_clock
        return [
            self.processes.create_process(
                "AlarmProcess",
                priority=3,
                memory_required=64,
                cpu_burst_time=2,
                arrival_time=clock,
            ),
            self.processes.create_process(
                "TemperatureSensorProcess",
                priority=2,
                memory_required=128,
                cpu_burst_time=3,
                arrival_time=clock,
            ),
            self.processes.create_process(
                "CameraProcess",
                priority=1,
                memory_required=512,
                cpu_burst_time=12,
                arrival_time=clock,
            ),
            self.processes.create_process(
                "DataLoggerProcess",
                priority=2,
                memory_required=256,
                cpu_burst_time=6,
                arrival_time=clock,
            ),
            self.processes.create_process(
                "NetworkSyncProcess",
                priority=1,
                memory_required=384,
                cpu_burst_time=8,
                arrival_time=clock,
            ),
        ]

    def _create_phase2_iot_processes(self) -> List[PCB]:
        """Small IoT-style workload sized to stress a tiny physical frame pool."""
        clock = self._sim_clock
        return [
            self.processes.create_process(
                "CameraCaptureService",
                priority=2,
                memory_required=256,
                cpu_burst_time=10,
                arrival_time=clock,
            ),
            self.processes.create_process(
                "AlarmMonitorDaemon",
                priority=3,
                memory_required=128,
                cpu_burst_time=8,
                arrival_time=clock,
            ),
            self.processes.create_process(
                "SensorFusionWorker",
                priority=1,
                memory_required=128,
                cpu_burst_time=10,
                arrival_time=clock,
            ),
            self.processes.create_process(
                "EdgeDataLogger",
                priority=1,
                memory_required=128,
                cpu_burst_time=6,
                arrival_time=clock,
            ),
            self.processes.create_process(
                "NetworkSyncAgent",
                priority=2,
                memory_required=128,
                cpu_burst_time=6,
                arrival_time=clock,
            ),
        ]

    def _run_until_all_terminated(self) -> None:
        for _ in range(100_000):
            if all(p.state == ProcessState.TERMINATED for p in self.processes.list_processes()):
                break
            if self._cpu_step():
                continue
            if any(p.state != ProcessState.TERMINATED for p in self.processes.list_processes()):
                _log("run: CPU idle but not all processes terminated — stopping")
            break

    def _collect_policy_stats(self, policy_label: str) -> PolicyRunStats:
        done = [
            p
            for p in self.processes.list_processes()
            if p.state == ProcessState.TERMINATED
            and p.waiting_time is not None
            and p.turnaround_time is not None
            and p.response_time is not None
        ]
        n = len(done)
        if n == 0:
            return PolicyRunStats(policy_label, 0.0, 0.0, 0.0, self.context_switches)
        aw = sum(p.waiting_time or 0 for p in done) / n
        at = sum(p.turnaround_time or 0 for p in done) / n
        ar = sum(p.response_time or 0 for p in done) / n
        return PolicyRunStats(policy_label, aw, at, ar, self.context_switches)

    def run_phase1_demo(self, *, rr_quantum: int = 3) -> None:
        """Run identical IoT workload under FIFO then RR; print comparison (also logged)."""
        _log("=== Phase 1 demo: IoT workload — FIFO vs Round Robin ===")

        fifo_sim = Simulation(total_memory=self.memory.total_memory, enable_memory_access=False)
        fifo_sim.scheduler = create_scheduler("fifo")
        fifo_sim._reset_cpu_execution_state()
        procs = fifo_sim._create_phase1_iot_processes()
        for p in procs:
            if not fifo_sim.memory.allocate(p.pid, p.memory_required):
                _log(f"phase1 FIFO: memory allocation failed for pid={p.pid}")
        for p in procs:
            fifo_sim.processes.change_state(p.pid, ProcessState.READY)
            fifo_sim.scheduler.enqueue(p)
        fifo_sim._run_until_all_terminated()
        fifo_stats = fifo_sim._collect_policy_stats("FIFO (q=1)")

        rr_sim = Simulation(total_memory=self.memory.total_memory, enable_memory_access=False)
        rr_sim.scheduler = create_scheduler("rr", quantum=rr_quantum)
        rr_sim._reset_cpu_execution_state()
        procs2 = rr_sim._create_phase1_iot_processes()
        for p in procs2:
            if not rr_sim.memory.allocate(p.pid, p.memory_required):
                _log(f"phase1 RR: memory allocation failed for pid={p.pid}")
        for p in procs2:
            rr_sim.processes.change_state(p.pid, ProcessState.READY)
            rr_sim.scheduler.enqueue(p)
        rr_sim._run_until_all_terminated()
        rr_stats = rr_sim._collect_policy_stats(f"RR (q={rr_quantum})")

        header = (
            f"{'Policy':<18} | {'Avg Waiting':>12} | {'Avg Turnaround':>15} | "
            f"{'Avg Response':>13} | {'Context Switches':>18}"
        )
        row_fifo = (
            f"{fifo_stats.policy_label:<18} | {fifo_stats.avg_waiting:12.2f} | "
            f"{fifo_stats.avg_turnaround:15.2f} | {fifo_stats.avg_response:13.2f} | "
            f"{fifo_stats.context_switches:18d}"
        )
        row_rr = (
            f"{rr_stats.policy_label:<18} | {rr_stats.avg_waiting:12.2f} | "
            f"{rr_stats.avg_turnaround:15.2f} | {rr_stats.avg_response:13.2f} | "
            f"{rr_stats.context_switches:18d}"
        )
        print(header)
        print(row_fifo)
        print(row_rr)
        _log(header)
        _log(row_fifo)
        _log(row_rr)
        _log("=== Phase 1 demo end ===")

    def run_phase2_demo(self) -> None:
        """IoT processes, paging, faults, BLOCKED/READY, scheduler continues, FIFO replacement."""
        _log("=== Phase 2 demo: paging, page faults, scheduler + FIFO replacement ===")
        sim = Simulation(total_memory=768, enable_memory_access=True)
        sim.memory.fault_handling_ticks = 3
        sim.scheduler = create_scheduler("rr", quantum=2)
        sim._reset_cpu_execution_state()

        procs = sim._create_phase2_iot_processes()
        for p in procs:
            if not sim.memory.allocate(p.pid, p.memory_required):
                _log(f"phase2: memory reservation failed for pid={p.pid}")
        for p in procs:
            sim.processes.change_state(p.pid, ProcessState.READY)
            sim.scheduler.enqueue(p)

        sim._run_until_all_terminated()
        _log("=== Phase 2 demo end ===")

    def _phase3_cpu_ticks(self, ticks: int) -> None:
        """Advance the CPU simulator for a fixed number of ticks (Phase 3 interleaving)."""
        for _ in range(ticks):
            if not self._cpu_step():
                if self.memory.pending_fault_jobs() > 0 or self._pending_file_io:
                    continue
                break

    def run_phase3_demo(self) -> None:
        """
        Deterministic producer–consumer narrative: Camera produces, DataLogger consumes,
        TemperatureSensor runs on the CPU while peers are BLOCKED on semaphores/mutex.
        """
        _log("=== Phase 3 demo: mutex + semaphores + bounded buffer (scripted) ===")
        self.enable_memory_access = False
        self._reset_cpu_execution_state()
        self.scheduler = create_scheduler("rr", quantum=2)

        camera = self.processes.create_process(
            "CameraProcess",
            priority=2,
            memory_required=256,
            cpu_burst_time=14,
            arrival_time=self._sim_clock,
        )
        logger = self.processes.create_process(
            "DataLoggerProcess",
            priority=1,
            memory_required=256,
            cpu_burst_time=14,
            arrival_time=self._sim_clock,
        )
        temp = self.processes.create_process(
            "TemperatureSensorProcess",
            priority=2,
            memory_required=128,
            cpu_burst_time=10,
            arrival_time=self._sim_clock,
        )
        for p in (camera, logger, temp):
            if not self.memory.allocate(p.pid, p.memory_required):
                _log(f"phase3: memory allocation failed for pid={p.pid}")

        for p in (camera, logger, temp):
            self.processes.change_state(p.pid, ProcessState.READY)
            self.scheduler.enqueue(p)

        buf = SharedBuffer[str](2, name="iot_frame_buffer")
        mtx = Lock("buffer_mutex")
        empty = CountingSemaphore("empty_slots", 2)
        full = CountingSemaphore("full_slots", 0)
        ports = self._make_sync_ports()

        def produce_frame(pid: int, tag: str) -> None:
            _log(f"phase3 script: pid={pid} begin produce {tag!r}")
            assert empty.wait(pid, ports), "expected empty slot"
            assert mtx.acquire(pid, ports), "expected mutex"
            buf.produce(tag)
            mtx.release(pid, ports)
            full.signal(pid, ports)
            _log(f"phase3 script: pid={pid} finished produce {tag!r}")

        def consume_frame(pid: int) -> str:
            _log(f"phase3 script: pid={pid} begin consume")
            assert full.wait(pid, ports), "expected full slot"
            assert mtx.acquire(pid, ports), "expected mutex"
            item = buf.consume()
            mtx.release(pid, ports)
            empty.signal(pid, ports)
            _log(f"phase3 script: pid={pid} finished consume {item!r}")
            return item

        produce_frame(camera.pid, "frame-A")
        produce_frame(camera.pid, "frame-B")
        _log("phase3 script: third produce should block Camera on empty_slots")
        assert not empty.wait(camera.pid, ports), "Camera should block when buffer is full"

        _log("phase3 script: run CPU while Camera is BLOCKED - TemperatureSensor should make progress")
        self._phase3_cpu_ticks(6)

        _log("phase3 script: DataLogger consumes one frame -> signals empty -> Camera unblocks")
        consume_frame(logger.pid)

        assert empty.wait(camera.pid, ports), "Camera should complete wait after empty signal"
        assert mtx.acquire(camera.pid, ports), "expected mutex for Camera"
        buf.produce("frame-C")
        mtx.release(camera.pid, ports)
        full.signal(camera.pid, ports)

        self._phase3_cpu_ticks(4)
        consume_frame(logger.pid)
        consume_frame(logger.pid)

        _log("phase3 script: drain remaining CPU bursts (all processes still READY/RUNNING)")
        self._run_until_all_terminated()
        _log("=== Phase 3 demo end ===")

    def run_phase4_demo(self) -> None:
        """
        IoT file traffic: temperature + camera logs, shared data file append/read,
        at least one I/O block and one file-lock contention while RR runs other READY work.
        """
        _log("=== Phase 4 demo: hierarchical FS + open files + I/O latency + file locks ===")
        self.enable_memory_access = False
        self._reset_cpu_execution_state()
        self.scheduler = create_scheduler("rr", quantum=2)
        self.file_io_ticks = 3

        self.fs.set_simulation_tick(self._sim_clock)
        assert self.fs.mkdir("/logs", creator_pid=0)
        assert self.fs.mkdir("/config", creator_pid=0)
        assert self.fs.mkdir("/data", creator_pid=0)
        assert self.fs.create("/logs/temperature.log", creator_pid=0)
        assert self.fs.create("/logs/camera.log", creator_pid=0)
        assert self.fs.create("/config/device.cfg", creator_pid=0)
        assert self.fs.create("/data/network_sync.txt", creator_pid=0)
        self.fs.write("/config/device.cfg", "device=edge-01\n", writer_pid=0)

        temp = self.processes.create_process(
            "TemperatureSensorProcess",
            priority=2,
            memory_required=128,
            cpu_burst_time=20,
            arrival_time=self._sim_clock,
        )
        camera = self.processes.create_process(
            "CameraProcess",
            priority=2,
            memory_required=256,
            cpu_burst_time=20,
            arrival_time=self._sim_clock,
        )
        logger = self.processes.create_process(
            "DataLoggerProcess",
            priority=1,
            memory_required=256,
            cpu_burst_time=20,
            arrival_time=self._sim_clock,
        )
        net = self.processes.create_process(
            "NetworkSyncProcess",
            priority=1,
            memory_required=384,
            cpu_burst_time=20,
            arrival_time=self._sim_clock,
        )
        for p in (temp, camera, logger, net):
            if not self.memory.allocate(p.pid, p.memory_required):
                _log(f"phase4: memory allocation failed for pid={p.pid}")
        for p in (temp, camera, logger, net):
            self.processes.change_state(p.pid, ProcessState.READY)
            self.scheduler.enqueue(p)

        _log("phase4 script: TemperatureSensor blocks on disk write; scheduler should pick other READY PIDs")
        assert self.file_blocking_write(temp.pid, "/logs/temperature.log", "temp=21.5C\n") == "io_started"
        self._phase4_cpu_ticks(4)

        _log("phase4 script: Camera writes its own log file (different path, no lock conflict)")
        assert self.file_blocking_write(camera.pid, "/logs/camera.log", "frame=jpeg-001\n") == "io_started"
        self._phase4_cpu_ticks(2)

        _log("phase4 script: DataLogger appends fused sensor line to shared /data/network_sync.txt")
        assert (
            self.file_blocking_append(logger.pid, "/data/network_sync.txt", "temp=21.5C|cam=jpeg-001\n")
            == "io_started"
        )
        _log("phase4 script: NetworkSync read contends - logger still holds exclusive lock during I/O")
        assert self.file_blocking_read(net.pid, "/data/network_sync.txt") == "lock_blocked"
        self._phase4_cpu_ticks(6)
        assert self.file_blocking_read(net.pid, "/data/network_sync.txt") == "io_started"
        self._phase4_cpu_ticks(4)

        listed = self.fs.list_dir("/logs")
        assert listed is not None and "temperature.log" in listed and "camera.log" in listed
        _log(f"phase4 script: list_dir /logs -> {listed!r}")

        self._run_until_all_terminated()
        _log("=== Phase 4 demo end ===")

    def _phase5_cpu_ticks(self, ticks: int) -> None:
        """Advance CPU simulation for a fixed number of ticks (Phase 5 scripted interleaving)."""
        for _ in range(ticks):
            if not self._cpu_step():
                if self.memory.pending_fault_jobs() > 0 or self._pending_file_io:
                    continue
                break

    def _phase5_collect_metrics(
        self,
        label: str,
        *,
        data_logger_remaining_before_release: int = -1,
        network_remaining_before_release: int = -1,
    ) -> Phase5StressMetrics:
        def first_dispatch(exact: str) -> Optional[int]:
            for p in self.processes.list_processes():
                if p.name == exact:
                    return p.start_time
            return None

        return Phase5StressMetrics(
            label=label,
            context_switches=self.context_switches,
            sim_clock=self._sim_clock,
            alarm_first_dispatch_tick=first_dispatch("AlarmMonitorProcess"),
            data_logger_first_dispatch_tick=first_dispatch("DataLoggerProcess"),
            network_first_dispatch_tick=first_dispatch("NetworkSyncProcess"),
            data_logger_remaining_before_release=data_logger_remaining_before_release,
            network_remaining_before_release=network_remaining_before_release,
        )

    def _phase5_run_stress_scenario(self, *, mutex_inheritance: bool) -> Phase5StressMetrics:
        """Heavy contention: LOW holds mutex; HIGH blocks; MEDIUM competes; aging enabled."""
        self.enable_memory_access = False
        self._reset_cpu_execution_state()
        self.mutex_inheritance_enabled = mutex_inheritance
        self.priority_aging_enabled = True
        self.aging_interval = 4
        self.scheduler = create_scheduler("priority")

        data_logger = self.processes.create_process(
            "DataLoggerProcess",
            priority=1,
            memory_required=256,
            cpu_burst_time=24,
            arrival_time=self._sim_clock,
        )
        network = self.processes.create_process(
            "NetworkSyncProcess",
            priority=2,
            memory_required=384,
            cpu_burst_time=24,
            arrival_time=self._sim_clock,
        )
        alarm = self.processes.create_process(
            "AlarmMonitorProcess",
            priority=3,
            memory_required=128,
            cpu_burst_time=24,
            arrival_time=self._sim_clock,
        )
        for p in (data_logger, network, alarm):
            if not self.memory.allocate(p.pid, p.memory_required):
                _log(f"phase5 stress: memory allocation failed for pid={p.pid}")

        sensor = Lock("sensor_lock")
        assert sensor.acquire(data_logger.pid, None) is True
        _log(
            "phase5 stress: LOW (DataLogger) holds sensor_lock; HIGH (Alarm) will block on it; "
            "MEDIUM (NetworkSync) stays READY and can consume CPU while inheritance is OFF."
        )

        for p in (data_logger, network, alarm):
            self.processes.change_state(p.pid, ProcessState.READY)
            self.scheduler.enqueue(p)

        ports = self._make_sync_ports()
        assert sensor.acquire(alarm.pid, ports) is False
        _log("phase5 stress: Alarm blocked on sensor_lock — priority inversion chain established.")

        self._phase5_cpu_ticks(22)
        dl_rem = data_logger.remaining_time
        net_rem = network.remaining_time
        assert sensor.release(data_logger.pid, ports) is True
        _log("phase5 stress: DataLogger releases sensor_lock (scripted) so waiters can make progress.")
        self._phase5_cpu_ticks(12)
        return self._phase5_collect_metrics(
            "inheritance ON" if mutex_inheritance else "inheritance OFF",
            data_logger_remaining_before_release=dl_rem,
            network_remaining_before_release=net_rem,
        )

    def run_phase5_demo(self) -> None:
        """
        Engineering challenge: priority inversion, PriorityScheduler, inheritance, aging,
        stress metrics (before/after table), presentation logs.
        """
        _log("=== Phase 5 demo: inversion + inheritance + aging + stress metrics ===")

        _log("--- Part 1: Mandatory inversion (inheritance OFF, aging OFF for a clear baseline) ---")
        self.enable_memory_access = False
        self._reset_cpu_execution_state()
        self.mutex_inheritance_enabled = False
        self.priority_aging_enabled = False
        self.aging_interval = 5
        self.scheduler = create_scheduler("priority")

        data_logger = self.processes.create_process(
            "DataLoggerProcess",
            priority=1,
            memory_required=256,
            cpu_burst_time=20,
            arrival_time=self._sim_clock,
        )
        network = self.processes.create_process(
            "NetworkSyncProcess",
            priority=2,
            memory_required=384,
            cpu_burst_time=20,
            arrival_time=self._sim_clock,
        )
        alarm = self.processes.create_process(
            "AlarmMonitorProcess",
            priority=3,
            memory_required=128,
            cpu_burst_time=20,
            arrival_time=self._sim_clock,
        )
        for p in (data_logger, network, alarm):
            if not self.memory.allocate(p.pid, p.memory_required):
                _log(f"phase5: memory allocation failed for pid={p.pid}")

        sensor = Lock("sensor_lock")
        assert sensor.acquire(data_logger.pid, None) is True
        _log(
            "phase5 script: LOW (DataLogger, pri=1) already holds sensor_lock (sensor / shared buffer)."
        )

        for p in (data_logger, network, alarm):
            self.processes.change_state(p.pid, ProcessState.READY)
            self.scheduler.enqueue(p)

        ports = self._make_sync_ports()
        _log(
            "phase5 script: PriorityScheduler picks HIGH (Alarm, pri=3) first — Alarm tries sensor_lock."
        )
        assert sensor.acquire(alarm.pid, ports) is False
        _log(
            "phase5 script: Alarm BLOCKED on mutex — without inheritance, "
            "LOW keeps base effective=1 so MEDIUM (NetworkSync, pri=2) wins the CPU over LOW."
        )

        self._phase5_cpu_ticks(8)
        dl_rem_1 = data_logger.remaining_time
        net_rem_1 = network.remaining_time
        _log(
            f"phase5 snapshot (inheritance OFF after 8 ticks): "
            f"DataLogger remaining={dl_rem_1} NetworkSync remaining={net_rem_1} "
            f"(expect NetworkSync to make more progress than DataLogger)."
        )

        _log("--- Part 2: Same narrative WITH priority inheritance enabled ---")
        self.enable_memory_access = False
        self._reset_cpu_execution_state()
        self.mutex_inheritance_enabled = True
        self.priority_aging_enabled = False
        self.aging_interval = 5
        self.scheduler = create_scheduler("priority")

        data_logger2 = self.processes.create_process(
            "DataLoggerProcess",
            priority=1,
            memory_required=256,
            cpu_burst_time=20,
            arrival_time=self._sim_clock,
        )
        network2 = self.processes.create_process(
            "NetworkSyncProcess",
            priority=2,
            memory_required=384,
            cpu_burst_time=20,
            arrival_time=self._sim_clock,
        )
        alarm2 = self.processes.create_process(
            "AlarmMonitorProcess",
            priority=3,
            memory_required=128,
            cpu_burst_time=20,
            arrival_time=self._sim_clock,
        )
        for p in (data_logger2, network2, alarm2):
            if not self.memory.allocate(p.pid, p.memory_required):
                _log(f"phase5: memory allocation failed for pid={p.pid}")

        sensor2 = Lock("sensor_lock")
        assert sensor2.acquire(data_logger2.pid, None) is True
        for p in (data_logger2, network2, alarm2):
            self.processes.change_state(p.pid, ProcessState.READY)
            self.scheduler.enqueue(p)
        ports2 = self._make_sync_ports()
        assert sensor2.acquire(alarm2.pid, ports2) is False
        _log(
            "phase5 script: inheritance boosts LOW's effective priority to satisfy the blocked HIGH waiter."
        )

        self._phase5_cpu_ticks(8)
        dl_rem_2 = data_logger2.remaining_time
        net_rem_2 = network2.remaining_time
        _log(
            f"phase5 snapshot (inheritance ON after 8 ticks): "
            f"DataLogger remaining={dl_rem_2} NetworkSync remaining={net_rem_2} "
            f"(expect DataLogger to run ahead of NetworkSync so the lock can be released)."
        )

        _log("--- Part 3: Stress + deterministic metrics (aging ON) — two full Simulation runs ---")
        sim_off = Simulation(total_memory=4096)
        m_off = sim_off._phase5_run_stress_scenario(mutex_inheritance=False)
        sim_on = Simulation(total_memory=4096)
        m_on = sim_on._phase5_run_stress_scenario(mutex_inheritance=True)

        header = (
            f"{'Mode':<22} | {'Ctx sw':>8} | {'Clock':>7} | "
            f"{'Alarm 1st run':>12} | {'Log 1st run':>11} | {'Net 1st run':>11} | "
            f"{'DL rem @rel':>11} | {'Net rem @rel':>12}"
        )

        def row(m: Phase5StressMetrics) -> str:
            a = "-" if m.alarm_first_dispatch_tick is None else str(m.alarm_first_dispatch_tick)
            d = "-" if m.data_logger_first_dispatch_tick is None else str(m.data_logger_first_dispatch_tick)
            n = "-" if m.network_first_dispatch_tick is None else str(m.network_first_dispatch_tick)
            return (
                f"{m.label:<22} | {m.context_switches:8d} | {m.sim_clock:7d} | "
                f"{a:>12} | {d:>11} | {n:>11} | "
                f"{m.data_logger_remaining_before_release:11d} | {m.network_remaining_before_release:12d}"
            )

        print(header)
        print(row(m_off))
        print(row(m_on))
        _log(header)
        _log(row(m_off))
        _log(row(m_on))
        _log("=== Phase 5 demo end ===")
