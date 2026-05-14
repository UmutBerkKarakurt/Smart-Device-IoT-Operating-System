"""Wires core subsystems and runs Phase 0–2 demos (optional paged memory access)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from os_core.logger import get_logger
from concurrency.locks import Lock
from filesystem.file_system import FileSystem
from memory.memory_manager import MemoryManager, deterministic_logical_address
from process.pcb import PCB, ProcessState
from process.process_manager import ProcessManager
from process.scheduler import Scheduler, create_scheduler

_log = get_logger("Simulation")
_sch = get_logger("Scheduler")


@dataclass
class PolicyRunStats:
    policy_label: str
    avg_waiting: float
    avg_turnaround: float
    avg_response: float
    context_switches: int


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

    def _reset_cpu_execution_state(self) -> None:
        self._sim_clock = 0
        self._current_pcb = None
        self._ticks_in_slice = 0
        self._cs_prev_pid = None
        self.context_switches = 0

    def _effective_quantum(self) -> int:
        return int(getattr(self.scheduler, "quantum", 1))

    def _note_context_switch_if_needed(self, pid: int, name: str) -> None:
        if self._cs_prev_pid != pid:
            self.context_switches += 1
            _sch(f"Context switch: pid={pid} {name} RUNNING")
        self._cs_prev_pid = pid

    def _cpu_step(self) -> bool:
        """One simulation tick: advance page-in timers, then run CPU (with optional memory access)."""
        self.memory.advance_fault_timers()

        quantum = self._effective_quantum()
        table = self.processes.pid_table()

        while True:
            if self._current_pcb is None:
                pcb = self.scheduler.pick_next(table)
                if pcb is None:
                    if self.memory.pending_fault_jobs() > 0:
                        self._sim_clock += 1
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
            self._sim_clock += 1

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
        lock.acquire(p1.pid)
        lock.acquire(p2.pid)
        lock.release(p1.pid)
        lock.acquire(p2.pid)
        lock.release(p2.pid)

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
