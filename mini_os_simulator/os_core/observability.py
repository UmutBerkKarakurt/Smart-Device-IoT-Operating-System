"""Unified timeline, metrics dashboard, and analysis helpers (final integration layer)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, DefaultDict, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

from process.pcb import ProcessState

EventCategory = Literal[
    "SCHEDULER",
    "MEMORY",
    "FILESYSTEM",
    "CONCURRENCY",
    "PRIORITY",
    "PROCESS",
]


@dataclass(frozen=True)
class SystemEvent:
    """One row in the unified simulation timeline."""

    tick: int
    category: EventCategory
    event_type: str
    description: str
    related_pids: Tuple[int, ...] = ()


@dataclass
class SystemMetricsDashboard:
    """Aggregate counters and averages (updated alongside ``SystemEvent`` recording)."""

    total_context_switches: int = 0
    total_page_faults: int = 0
    total_page_replacements: int = 0
    total_file_io_completed: int = 0
    total_blocked_transitions: int = 0
    total_semaphore_waits: int = 0
    total_lock_contentions: int = 0
    total_priority_inheritance_activations: int = 0
    total_aging_events: int = 0
    total_scheduler_dispatches: int = 0
    page_faults_by_pid: DefaultDict[int, int] = field(default_factory=lambda: defaultdict(int))
    blocked_by_pid: DefaultDict[int, int] = field(default_factory=lambda: defaultdict(int))
    file_lock_contention_by_path: DefaultDict[str, int] = field(default_factory=lambda: defaultdict(int))

    def reset(self) -> None:
        self.total_context_switches = 0
        self.total_page_faults = 0
        self.total_page_replacements = 0
        self.total_file_io_completed = 0
        self.total_blocked_transitions = 0
        self.total_semaphore_waits = 0
        self.total_lock_contentions = 0
        self.total_priority_inheritance_activations = 0
        self.total_aging_events = 0
        self.total_scheduler_dispatches = 0
        self.page_faults_by_pid.clear()
        self.blocked_by_pid.clear()
        self.file_lock_contention_by_path.clear()


def format_metrics_table(
    metrics: SystemMetricsDashboard,
    *,
    avg_waiting: float,
    avg_turnaround: float,
    avg_response: float,
    sink: Callable[[str], None],
) -> None:
    sink("================ SYSTEM METRICS ================")
    sink(f"Context Switches: {metrics.total_context_switches}")
    sink(f"Scheduler Dispatches: {metrics.total_scheduler_dispatches}")
    sink(f"Page Faults: {metrics.total_page_faults}")
    sink(f"Page Replacements: {metrics.total_page_replacements}")
    sink(f"File I/O Operations (completed): {metrics.total_file_io_completed}")
    sink(f"Process BLOCKED Transitions: {metrics.total_blocked_transitions}")
    sink(f"Semaphore Blocking Waits: {metrics.total_semaphore_waits}")
    sink(f"Lock Contentions (mutex + file): {metrics.total_lock_contentions}")
    sink(f"Priority Inheritance Activations: {metrics.total_priority_inheritance_activations}")
    sink(f"Aging Events: {metrics.total_aging_events}")
    sink(f"Average Waiting Time: {avg_waiting:.1f}")
    sink(f"Average Turnaround Time: {avg_turnaround:.1f}")
    sink(f"Average Response Time: {avg_response:.1f}")
    sink("================================================")


def format_grouped_timeline(
    events: Sequence[SystemEvent],
    *,
    sink: Callable[[str], None],
    max_events: Optional[int] = None,
) -> None:
    """Presentation-friendly grouped dump (most recent last)."""
    seq = list(events)
    if max_events is not None and len(seq) > max_events:
        seq = seq[-max_events:]
    order: Tuple[EventCategory, ...] = (
        "SCHEDULER",
        "MEMORY",
        "FILESYSTEM",
        "CONCURRENCY",
        "PRIORITY",
        "PROCESS",
    )
    by_cat: Dict[str, List[SystemEvent]] = {c: [] for c in order}
    for e in seq:
        by_cat.setdefault(e.category, []).append(e)
    sink("")
    sink("================ UNIFIED TIMELINE (grouped) ================")
    for cat in order:
        evs = by_cat.get(cat, [])
        if not evs:
            continue
        sink(f"--- {cat} ---")
        for e in evs:
            pids = f" pids={list(e.related_pids)}" if e.related_pids else ""
            sink(f"  [TICK {e.tick}] {e.event_type}: {e.description}{pids}")
    sink("==============================================================")


def print_log_category_banner(category: str, *, sink: Callable[[str], None]) -> None:
    """Optional banner before a batch of related logs."""
    sink(f"=== {category} ===")


def analyze_system(
    events: Sequence[SystemEvent],
    metrics: SystemMetricsDashboard,
    *,
    policy_label: str = "",
) -> Dict[str, object]:
    """
    Deterministic, educational summaries from timeline + dashboard counters.
    """
    max_wait_faults: Tuple[Optional[int], int] = (None, -1)
    for pid, c in metrics.page_faults_by_pid.items():
        if c > max_wait_faults[1]:
            max_wait_faults = (pid, c)
    max_blocked: Tuple[Optional[int], int] = (None, -1)
    for pid, c in metrics.blocked_by_pid.items():
        if c > max_blocked[1]:
            max_blocked = (pid, c)
    max_file_lock: Tuple[Optional[str], int] = (None, -1)
    for path, c in metrics.file_lock_contention_by_path.items():
        if c > max_file_lock[1]:
            max_file_lock = (path, c)
    return {
        "policy": policy_label,
        "most_page_faults_pid": max_wait_faults[0],
        "most_page_faults_count": max_wait_faults[1],
        "most_blocked_pid": max_blocked[0],
        "most_blocked_count": max_blocked[1],
        "most_file_lock_contention_path": max_file_lock[0],
        "most_file_lock_contention_count": max_file_lock[1],
        "timeline_length": len(events),
    }


def format_analysis_report(analysis: Dict[str, object], *, sink: Callable[[str], None]) -> None:
    sink("")
    sink("================ SYSTEM ANALYSIS ================")
    pol = analysis.get("policy")
    if isinstance(pol, str) and pol:
        sink(f"Scheduler policy: {pol}")
    sink(
        f"Most page faults: pid={analysis.get('most_page_faults_pid')} "
        f"(count={analysis.get('most_page_faults_count')})"
    )
    sink(
        f"Most BLOCKED transitions: pid={analysis.get('most_blocked_pid')} "
        f"(count={analysis.get('most_blocked_count')})"
    )
    sink(
        f"Most file-lock contention: path={analysis.get('most_file_lock_contention_path')!r} "
        f"(count={analysis.get('most_file_lock_contention_count')})"
    )
    sink(f"Timeline events recorded: {analysis.get('timeline_length')}")
    sink("=================================================")


def scheduler_policy_comparison_text() -> str:
    """Static educational comparison (complements live metrics from ``run_phase1_demo``)."""
    return """
================ SCHEDULER POLICY COMPARISON (FIFO vs RR vs Priority) ================
FIFO (non-preemptive):
  Fairness: arrival-order service; short jobs behind long jobs can starve for response time.
  Responsiveness: poor for mixed criticalities; urgent IoT events wait behind long batches.
  Context switches: minimal (run each process to completion or natural block).
  Starvation risk: not among READY peers (strict ordering), but priority inversion still possible
    with locks (addressed separately via inheritance in this simulator).
  IoT suitability: acceptable for simple pipelines; poor when alarms and sensors share a CPU.

Round Robin (preemptive, quantum q):
  Fairness: time-sliced among READY processes; bounded wait before each gets another turn.
  Responsiveness: better for interactive / bursty workloads; latency scales with q and ready set size.
  Context switches: higher than FIFO; increases with shorter quantum.
  Starvation risk: low among equal-priority READY peers; aging still helps priority queues.
  IoT suitability: good default for mixed-rate sensors and loggers when no strict priority ladder.

Priority (preemptive by effective priority):
  Fairness: favors higher effective priority; lower priority may wait (mitigated here by aging).
  Responsiveness: excellent for alarm/camera vs logger when priorities reflect real deadlines.
  Context switches: moderate; depends on priority churn and blocking patterns.
  Starvation risk: inherent without aging; this project adds aging_bonus on READY peers.
  IoT suitability: strong when workloads are tiered (safety > telemetry > background sync).

Deterministic simulation: no RNG; repeatability for grading, demos, and regression tests.
====================================================================================
""".strip()


def collect_average_metrics_from_pcbs(pcbs: Iterable) -> Tuple[float, float, float]:
    """Average waiting / turnaround / response over terminated PCBs with finalized metrics."""
    done = [
        p
        for p in pcbs
        if getattr(p, "state", None) == ProcessState.TERMINATED
        and p.waiting_time is not None
        and p.turnaround_time is not None
        and p.response_time is not None
    ]
    if not done:
        return 0.0, 0.0, 0.0
    n = len(done)
    aw = sum(p.waiting_time or 0 for p in done) / n
    at = sum(p.turnaround_time or 0 for p in done) / n
    ar = sum(p.response_time or 0 for p in done) / n
    return aw, at, ar
