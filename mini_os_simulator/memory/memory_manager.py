"""Paged physical memory with demand loading, faults, and optional FIFO replacement."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

from os_core.logger import get_logger

_log = get_logger("Memory")


class PageFaultError(Exception):
    """Raised when a logical page is valid but not resident in physical memory."""

    def __init__(self, pid: int, logical_page: int) -> None:
        self.pid = pid
        self.logical_page = logical_page
        super().__init__(f"page fault pid={pid} page={logical_page}")


@dataclass
class FrameEntry:
    pid: int
    logical_page: int
    load_seq: int


@dataclass
class FaultJob:
    pid: int
    logical_page: int
    ticks_remaining: int


@dataclass
class MemoryAccessResult:
    """Result of a single logical memory access attempt."""

    kind: str  # "hit" | "fault"
    logical_address: int
    logical_page: int
    offset: int
    physical_address: Optional[int] = None
    physical_frame: Optional[int] = None


class MemoryPorts:
    """Optional callbacks wired from Simulation to avoid circular imports."""

    def __init__(self) -> None:
        self.clear_page_mapping: Optional[Callable[[int, int], None]] = None
        self.install_page_mapping: Optional[Callable[[int, int, int], None]] = None
        self.mark_ready_and_enqueue: Optional[Callable[[int], None]] = None


class MemoryManager:
    """Byte reservation (Phase 0 compat) + paged physical frames (Phase 2)."""

    def __init__(self, total_physical_memory: int, page_size: Optional[int] = None) -> None:
        if total_physical_memory < 0:
            raise ValueError("total_physical_memory must be non-negative")
        self.total_physical_memory = total_physical_memory
        # Alias for older code paths (Phase 0 / Phase 1).
        self.total_memory = total_physical_memory

        if page_size is None:
            if total_physical_memory >= 256:
                ps = 256
            else:
                ps = max(1, total_physical_memory // 4)
        else:
            ps = page_size
        if ps < 1:
            raise ValueError("page_size must be >= 1")
        self.page_size = ps

        self.total_frames = total_physical_memory // self.page_size
        self._frame_table: List[Optional[FrameEntry]] = [None] * self.total_frames
        self._free_frames: Deque[int] = deque(range(self.total_frames))

        # Byte reservation accounting (same semantics as Phase 0).
        self._reservation_bytes: Dict[int, int] = {}
        self.used_memory = 0

        # Logical address space size in pages per pid (demand-paged into frames).
        self._logical_page_count: Dict[int, int] = {}

        self._fault_jobs: List[FaultJob] = []
        self.ports = MemoryPorts()
        self.fault_handling_ticks: int = 3
        self._next_load_seq: int = 0

    def logical_page_count_for(self, pid: int) -> int:
        return int(self._logical_page_count.get(pid, 0))

    def pending_fault_jobs(self) -> int:
        return len(self._fault_jobs)

    def allocate(self, pid: int, amount: int) -> bool:
        if amount < 0:
            _log(f"allocate pid={pid}: reject negative amount={amount}")
            return False
        current = self._reservation_bytes.get(pid, 0)
        delta = amount - current
        if self.used_memory + delta > self.total_physical_memory:
            _log(
                f"allocate pid={pid}: reject need {delta} free "
                f"{self.total_physical_memory - self.used_memory} (want total {amount})"
            )
            return False
        self.used_memory += delta
        self._reservation_bytes[pid] = amount
        if amount == 0:
            self._logical_page_count[pid] = 0
        else:
            pages = max(1, (amount + self.page_size - 1) // self.page_size)
            self._logical_page_count[pid] = pages
        _log(
            f"allocate pid={pid}: reserved {amount} bytes, "
            f"{self._logical_page_count.get(pid, 0)} logical pages "
            f"(used {self.used_memory}/{self.total_physical_memory})"
        )
        if self.total_frames and (len(self._fault_jobs) == 0 or self.used_memory % (self.page_size * 4) == 0):
            self._log_frame_summary()
        return True

    def free(self, pid: int) -> int:
        had = self._reservation_bytes.pop(pid, 0)
        self.used_memory = max(0, self.used_memory - had)
        self._logical_page_count.pop(pid, None)
        self._fault_jobs = [j for j in self._fault_jobs if j.pid != pid]
        freed_frames = 0
        for fi, entry in enumerate(self._frame_table):
            if entry is not None and entry.pid == pid:
                self._frame_table[fi] = None
                self._free_frames.append(fi)
                freed_frames += 1
                if self.ports.clear_page_mapping:
                    self.ports.clear_page_mapping(pid, entry.logical_page)
        _log(
            f"free pid={pid}: released {had} bytes, freed {freed_frames} frames "
            f"(used {self.used_memory}/{self.total_physical_memory})"
        )
        return had

    @property
    def free_frame_count(self) -> int:
        return len(self._free_frames)

    def frame_table_snapshot(self) -> List[Optional[Tuple[int, int, int]]]:
        """(pid, logical_page, load_seq) or None per frame — for tests/introspection."""
        out: List[Optional[Tuple[int, int, int]]] = []
        for e in self._frame_table:
            if e is None:
                out.append(None)
            else:
                out.append((e.pid, e.logical_page, e.load_seq))
        return out

    def translate_address(
        self,
        pid: int,
        logical_address: int,
        page_table: Dict[int, int],
    ) -> Tuple[int, int, int, int]:
        """
        Returns (physical_address, logical_page, offset, frame).
        Raises PageFaultError if the logical page is valid but not resident.
        """
        lp_count = self._logical_page_count.get(pid, 0)
        if lp_count <= 0:
            raise ValueError(f"translate: pid={pid} has no logical address space")
        page = logical_address // self.page_size
        offset = logical_address % self.page_size
        if page < 0 or page >= lp_count:
            raise ValueError(
                f"translate: pid={pid} logical_address={logical_address} out of range "
                f"(pages=0..{lp_count - 1})"
            )
        if page not in page_table:
            raise PageFaultError(pid, page)
        frame = page_table[page]
        phys = frame * self.page_size + offset
        _log(f"pid={pid} logical={logical_address} -> page={page} offset={offset} -> frame={frame}")
        return phys, page, offset, frame

    def try_access(
        self,
        pid: int,
        logical_address: int,
        page_table: Dict[int, int],
    ) -> MemoryAccessResult:
        lp_count = self._logical_page_count.get(pid, 0)
        if lp_count <= 0:
            return MemoryAccessResult(
                kind="hit",
                logical_address=logical_address,
                logical_page=0,
                offset=0,
                physical_address=0,
                physical_frame=0,
            )
        page = logical_address // self.page_size
        offset = logical_address % self.page_size
        if page < 0 or page >= lp_count:
            page = page % lp_count
            offset = logical_address % self.page_size
            logical_address = page * self.page_size + offset
        if page not in page_table:
            return MemoryAccessResult(
                kind="fault",
                logical_address=logical_address,
                logical_page=page,
                offset=offset,
            )
        frame = page_table[page]
        phys = frame * self.page_size + offset
        _log(f"pid={pid} logical={logical_address} -> page={page} offset={offset} -> frame={frame}")
        return MemoryAccessResult(
            kind="hit",
            logical_address=logical_address,
            logical_page=page,
            offset=offset,
            physical_address=phys,
            physical_frame=frame,
        )

    def begin_fault_resolution(self, pid: int, logical_page: int) -> None:
        for j in self._fault_jobs:
            if j.pid == pid and j.logical_page == logical_page:
                return
        _log(f"PAGE FAULT pid={pid} page={logical_page}")
        self._fault_jobs.append(
            FaultJob(pid=pid, logical_page=logical_page, ticks_remaining=self.fault_handling_ticks)
        )

    def advance_fault_timers(self) -> None:
        """Advance all in-flight fault resolutions by one tick; complete any that reach 0."""
        if not self._fault_jobs:
            return
        still: List[FaultJob] = []
        for job in self._fault_jobs:
            job.ticks_remaining -= 1
            if job.ticks_remaining <= 0:
                self._complete_fault_load(job.pid, job.logical_page)
            else:
                still.append(job)
        self._fault_jobs = still

    def _complete_fault_load(self, pid: int, logical_page: int) -> None:
        if self.total_frames == 0:
            _log("exhaustion: no physical frames configured; cannot load page")
            return
        frame = self._pick_frame_for_load()
        if frame is None:
            _log("exhaustion: no free frame and eviction failed")
            return
        # If frame was occupied, mapping already cleared in _evict_fifo.
        self._next_load_seq += 1
        entry = FrameEntry(pid=pid, logical_page=logical_page, load_seq=self._next_load_seq)
        self._frame_table[frame] = entry
        _log(f"Loaded page {logical_page} into frame {frame} for pid={pid}")
        if self.ports.install_page_mapping:
            self.ports.install_page_mapping(pid, logical_page, frame)
        if self.ports.mark_ready_and_enqueue:
            self.ports.mark_ready_and_enqueue(pid)

    def _pick_frame_for_load(self) -> Optional[int]:
        if self._free_frames:
            return int(self._free_frames.popleft())
        return self._evict_fifo()

    def _evict_fifo(self) -> Optional[int]:
        """Evict the globally oldest loaded page by ``load_seq``; return freed frame index."""
        best_idx: Optional[int] = None
        best_seq: Optional[int] = None
        for fi, entry in enumerate(self._frame_table):
            if entry is None:
                continue
            if best_seq is None or entry.load_seq < best_seq:
                best_seq = entry.load_seq
                best_idx = fi
        if best_idx is None:
            return None
        victim = self._frame_table[best_idx]
        assert victim is not None
        _log(
            f"FIFO replacement: evict pid={victim.pid} page={victim.logical_page} "
            f"from frame {best_idx} (load_seq={victim.load_seq})"
        )
        self._frame_table[best_idx] = None
        if self.ports.clear_page_mapping:
            self.ports.clear_page_mapping(victim.pid, victim.logical_page)
        return best_idx

    def _log_frame_summary(self) -> None:
        used = self.total_frames - len(self._free_frames)
        _log(
            f"frame summary: {used}/{self.total_frames} frames in use, "
            f"{len(self._free_frames)} free, pending_faults={len(self._fault_jobs)}"
        )


def deterministic_logical_address(pid: int, tick_index: int, page_size: int, logical_page_count: int) -> int:
    """
    Reproducible logical address from pid and tick index (no RNG).

    Uses fixed integer mixing (pid/tick scalars) so pytest and ``python main.py`` traces
    are stable across runs and machines. Not a model of real program locality.
    """
    if logical_page_count <= 0 or page_size <= 0:
        return 0
    # Golden-ratio style mixing — stable across platforms.
    mixed = (pid * 2654435761 + tick_index * 2246822519) & 0xFFFFFFFF
    page = mixed % logical_page_count
    offset = (mixed // logical_page_count) % page_size
    return int(page * page_size + offset)
