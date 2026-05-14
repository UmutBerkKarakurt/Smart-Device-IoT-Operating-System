# Phase 2 Report — Memory Management (Paging, Faults, Scheduler)

## 1. What was implemented

Phase 2 adds **demand-paged physical memory** on top of the Phase 0 **byte reservation** model. Each process has a **logical page count** derived from its reserved bytes and a configurable **page size**. Resident pages are tracked in a **per-process page table** on the PCB. The **MemoryManager** owns the **frame table**, free-frame list, **page-fault service queue** (fault handling cost in ticks), and **global FIFO replacement** when no free frame exists. The **Simulation** optionally performs a **deterministic logical memory access** each CPU tick; on a fault, the running process becomes **BLOCKED**, the scheduler runs other **READY** processes, and when the fault timer expires the page is loaded (possibly after eviction), mappings are installed, and the process returns to **READY** and is re-enqueued.

## 2. Files changed / added

| Action | Path |
|--------|------|
| Extended | `memory/memory_manager.py` |
| Extended | `memory/__init__.py` |
| Extended | `process/pcb.py` |
| Extended | `process/scheduler.py` |
| Extended | `os_core/simulation.py` |
| Extended | `main.py` |
| Added | `tests/test_phase2_memory.py` |
| Added | `PHASE_2_REPORT.md` |

## 3. Paging explanation

Physical memory is divided into **fixed-size frames** (`total_physical_memory // page_size`). Logical memory for a process is divided into the same-sized **pages**; the number of logical pages is `ceil(reserved_bytes / page_size)` (at least one page when bytes > 0). Only a subset of pages may be **resident** in frames at once (**demand paging**). This matches how small IoT devices run many tasks with larger **virtual** footprints than **physical** RAM.

**Why paging was chosen:** It is the standard way to teach **translation**, **locality**, and **multiplexing** of physical RAM without simulating every byte as a distinct object. Pages give a clean unit for faults, replacement, and frame accounting while keeping the simulator small.

## 4. Page tables

**Design choice:** The mapping **logical page → physical frame** lives on the **PCB** as `page_table: Dict[int, int]`. The **MemoryManager** owns **which frame holds which (pid, logical_page)** in `frame_table`, and coordinates eviction and install/clear via **callbacks** (`MemoryPorts`) wired from `Simulation`.

**Justification:** Page tables are process-specific state in real OSes; keeping them on the PCB keeps the PCB the single place for “what this process can access.” The MemoryManager remains the authority on **physical** placement and **replacement**, and calls back into the simulation to mutate PCB tables without importing `ProcessManager` (avoids circular imports).

## 5. Address translation

`MemoryManager.translate_address(pid, logical_address, page_table)` computes `page = logical_address // page_size`, `offset = logical_address % page_size`, checks the page is within the process’s logical page count, and if `page` is in `page_table` returns the physical address `frame * page_size + offset`. If the page is valid but absent, it raises **`PageFaultError`**. `try_access` returns a **`MemoryAccessResult`** (`hit` / `fault`) for the simulation path.

## 6. Page fault lifecycle

1. Running process issues a logical access (`try_access`).
2. If the page is missing: log **PAGE FAULT**, process **RUNNING → BLOCKED**, a **`FaultJob`** is queued with `ticks_remaining = fault_handling_ticks` (default **3** in demos, configurable).
3. Other **READY** processes continue to run; if the ready queue is empty, time still advances so fault timers count down (`_cpu_step` idle path when faults are pending).
4. Each tick, `advance_fault_timers()` decrements all fault jobs; when a job hits zero, **`_complete_fault_load`** allocates a frame (or **FIFO-evicts**), updates the frame table, calls **`install_page_mapping`**, then **`mark_ready_and_enqueue`** (**BLOCKED → READY**, enqueue).
5. When the process runs again, the same logical address may **hit** in RAM.

## 7. Cross-component interaction

- **MemoryManager** does not import the scheduler; **`MemoryPorts`** callbacks are set in **`Simulation._wire_memory_ports`**.
- **Scheduler** skips **BLOCKED** and **TERMINATED** entries in **`pick_next`** so a blocked page waiter does not starve the queue.
- **Simulation._cpu_step** uses an inner loop: on fault it clears the current CPU, **does not** consume a CPU burst tick for that process, and immediately tries to dispatch another READY process on the **same simulation tick** when possible.

**Why faults need scheduler interaction:** A page fault is modeled as **blocking I/O**. If the scheduler kept running the faulting process, no other work would run and fault latency would not be overlapped with useful CPU time—unlike a real OS where the blocked thread sleeps and others run.

## 8. Memory exhaustion handling

When **no free frame** exists and a new page must be loaded, the simulator performs **global FIFO replacement**: evict the resident page with the **smallest `load_seq`** (oldest load time), log the eviction, clear the victim’s mapping via **`clear_page_mapping`**, then load the faulting page into that frame.

## 9. Page replacement

**FIFO (global) by load order** is implemented as required. It is simple, deterministic, and easy to log for teaching. Alternatives (LRU, clock) would need extra per-reference metadata; FIFO matches the assignment and stays readable in traces.

**Why this replacement strategy was chosen:** It satisfies the spec, is **O(n)** over a small frame count, and produces clear “oldest victim” narratives in logs without a random policy.

## 10. Trade-offs

- **Byte reservation** (Phase 0) and **physical frames** share the same `total_physical_memory` cap: a process cannot *reserve* more bytes than physical **bytes** even though demand paging could admit more **virtual** space in a real system. This preserves existing tests and keeps one knob for “RAM size.”
- **Fault handling** is a single global tick counter per job, not a separate I/O device queue.
- **`enable_memory_access`** defaults to **false** so Phase 1 metrics and tests remain unchanged; Phase 2 turns it on.

## 11. Example logs

Typical patterns from `python main.py` (Phase 2):

- `[Scheduler] pid=… BLOCKED on page fault (page=…)`
- `[Memory] PAGE FAULT pid=… page=…`
- `[Memory] Loaded page … into frame … for pid=…`
- `[Scheduler] pid=… page-in complete -> READY, re-enqueued`
- `[Memory] FIFO replacement: evict pid=… page=… from frame … (load_seq=…)`
- `[Memory] pid=… logical=… -> page=… offset=… -> frame=…` (translation hit)

## 12. Known limitations

- No **TLB**, no **multi-level page tables**, no **copy-on-write** or **shared pages**.
- **Replacement** is global FIFO, not per-process working sets.
- **Deterministic addresses** use a closed form from `(pid, sim_clock)`—not a real program trace.
- **Security / protection**: no separate kernel mappings or access-right bits.

## 13. Phase 3 suggestions

- **Working-set** or **LRU-approximation (clock)** replacement and per-process resident-set limits.
- **Prepaging** or explicit **`mmap`-style** regions for camera buffers.
- **Dirty-bit** write-back simulation and **write faults**.
- **Separate** “virtual byte limit” from physical RAM to model flash-backed swap.

---

### Explicit paragraphs (assignment)

**Why paging was chosen:** Paging frames the problem in units that match teaching goals (translation, faults, replacement) while keeping the codebase small; it extends Phase 0 reservations without discarding them.

**Why the FIFO replacement strategy was chosen:** It is mandated as the preferred exhaustion policy, is easy to reason about in logs, and avoids randomness that would hurt reproducibility in tests and demos.

**Why faults need scheduler interaction:** Faults are modeled as blocking events; overlapping fault latency with other processes’ CPU time demonstrates multiprogramming and avoids a trivial sequential “stop the world” simulator.

**What realism was simplified:** No disk queue depth, no bandwidth limits, no NUMA, no contended fault handler lock, and fault completion is a fixed number of ticks rather than variable I/O latency.

### Deterministic memory access

Logical addresses use `deterministic_logical_address(pid, sim_clock, page_size, logical_page_count)` in `memory/memory_manager.py` — a fixed mixing function of **pid** and **simulation clock** (no RNG), documented in code comments for reproducibility in tests and demos.

### Fault handling cost

Default **`fault_handling_ticks = 3`**: each fault job must count down that many **global simulation ticks** before the page is installed. Documented here and overridable on `MemoryManager` (Phase 2 demo sets it explicitly for clarity).
