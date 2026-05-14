"""Process Control Block definition."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple


class ProcessState(Enum):
    NEW = auto()
    READY = auto()
    RUNNING = auto()
    BLOCKED = auto()
    TERMINATED = auto()


@dataclass
class PCB:
    pid: int
    name: str
    state: ProcessState = ProcessState.NEW
    priority: int = 0
    # Phase 5: scheduling uses effective_priority (base + aging, boosted by mutex inheritance floor).
    aging_bonus: int = 0
    mutex_inheritance_floor: int = 0
    program_counter: int = 0
    memory_required: int = 0
    cpu_burst_time: int = 0
    remaining_time: int = 0
    # (fd, path, mode) entries — extended in Phase 4 for open file table bookkeeping.
    opened_files: List[Tuple[int, str, str]] = field(default_factory=list)
    # Phase 2: demand-paged logical pages resident in physical frames (logical_page -> frame_index).
    page_table: Dict[int, int] = field(default_factory=dict)
    # Scheduling metrics (abstract simulation ticks)
    arrival_time: int = 0
    start_time: Optional[int] = None
    completion_time: Optional[int] = None
    waiting_time: Optional[int] = None
    turnaround_time: Optional[int] = None
    response_time: Optional[int] = None

    def __post_init__(self) -> None:
        if self.remaining_time == 0 and self.cpu_burst_time > 0:
            self.remaining_time = self.cpu_burst_time

    @property
    def effective_priority(self) -> int:
        """Highest of (base + aging bonus) and any mutex inheritance floor from blocked high-priority waiters."""
        return max(self.priority + self.aging_bonus, self.mutex_inheritance_floor)

    def on_first_cpu_dispatch(self, clock: int) -> None:
        """Record first transition to RUNNING (first tick of a CPU slice)."""
        if self.start_time is None:
            self.start_time = clock

    def finalize_metrics(self, completion_clock: int) -> None:
        """Set completion time and derived metrics (call once when remaining_time hits 0)."""
        self.completion_time = completion_clock
        self.turnaround_time = completion_clock - self.arrival_time
        if self.start_time is not None:
            self.response_time = self.start_time - self.arrival_time
        self.waiting_time = self.turnaround_time - self.cpu_burst_time
