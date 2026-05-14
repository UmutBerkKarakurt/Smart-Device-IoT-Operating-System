# Final Integrated Report — Smart Device / IoT Operating System Simulator

This document is the **capstone-style** summary of the full project. It is **not** a paste-up of phase reports; it integrates concepts across phases into one narrative.

---

## 1. Project overview

The simulator models a **small embedded-style OS** for teaching: processes, a ready queue, CPU time, **demand-paged memory**, **mutexes and counting semaphores**, a **hierarchical file system** with **exclusive file locks** and **blocking I/O**, and **priority scheduling** with **aging** and **priority inheritance** to mitigate mutex-induced inversion.

Everything runs in a **single-threaded, deterministic tick loop** (no real threads, no randomness). That choice favors **repeatable labs, demos, and tests** over wall-clock realism.

---

## 2. Smart device / IoT OS concept

Workloads are named and sized like **edge IoT** roles: alarm monitors, cameras, temperature-style sensors, data loggers, and network sync agents. They compete for **CPU**, **RAM frames**, **shared buffers**, and **log/config files**. The narrative matches how a constrained device multiplexes **safety-critical**, **latency-sensitive**, and **best-effort** tasks.

---

## 3. Architecture explanation

The `Simulation` class in `os_core/simulation.py` is the **orchestrator**: it advances a global tick, runs the scheduler, applies memory accesses, drains page-fault and file-I/O timers, and invokes **ports** (`SyncPorts`, `FileSystemPorts`, `MemoryPorts`) so subsystems stay **modular** (no circular imports). See **`ARCHITECTURE_DIAGRAMS.md`** for diagrams.

---

## 4. Scheduling subsystem

Three policies are supported: **FIFO**, **Round Robin** (quantum), and **Priority** (effective priority with documented tie-breaks). Metrics such as **waiting, turnaround, response**, and **context switches** are recorded on PCBs and compared in Phase 1 style runs. The final phase adds a **unified timeline** and **metrics dashboard** that mirror context switches and dispatches for presentation.

---

## 5. Memory subsystem

Byte **reservation** stays compatible with early phases; **paging** adds logical pages, **page faults**, a fault-service timer, and **FIFO replacement** when frames are full. Address streams use a **deterministic mixing function** (not a trace from real programs) so behavior is stable across machines.

---

## 6. Synchronization subsystem

**Mutexes** (FIFO waiters) and **counting semaphores** (handoff semantics on `signal`) integrate with the scheduler through **block/wake** callbacks so a process can leave the ready queue while logically waiting. A **bounded buffer** demo shows producer–consumer ordering without real concurrency bugs from threads.

---

## 7. File system subsystem

Paths are **POSIX-like** with directories, metadata, and **per-file exclusive locks**. **Blocking read/write/append/delete** queues an I/O job for a fixed number of ticks, holds the lock for the duration, then releases and wakes the process. **Open file table** bookkeeping ties descriptors to PCBs for narrative completeness.

---

## 8. Cross-component interaction

The **central idea** is that **blocking** is unified: whether the reason is a **page fault**, **semaphore**, **mutex**, or **file lock / disk latency**, the process leaves **RUNNING/READY**, the **scheduler picks someone else**, and later a **timer or partner primitive** calls **wake** so the process returns to **READY**.

---

## 9. Priority inversion challenge

Classic **priority inversion** appears when a **low-priority** task holds a mutex needed by a **high-priority** task while a **medium** task runs. The simulator demonstrates this under **PriorityScheduler**, then shows **priority inheritance** raising the holder’s **effective** priority so the lock can be released before unrelated medium work steals the CPU.

---

## 10. Failure scenarios (modeled, not random)

Examples include: **page faults under memory pressure** (replacement), **semaphore wait on full buffer**, **mutex contention**, **file lock contention during I/O**, and **starvation risk** on strict priority queues—mitigated by **aging** on READY processes when priority scheduling is active.

---

## 11. Trade-offs accepted

| Goal | Trade-off |
|------|-----------|
| Determinism | No statistical I/O or randomized workloads |
| Teaching clarity | Simplified MMU (single-level page table per PCB, global FIFO eviction) |
| Single-threaded code | Concurrency is logical, not parallel—no races, but not a perf model |
| Small codebase | No network stack, drivers, or user/kernel split beyond narration |

---

## 12. Metrics analysis

The **final phase** adds `SystemEvent` timeline entries (dispatch, context switch, faults, replacement, I/O, lock/semaphore events, inheritance, aging) and a **`SystemMetricsDashboard`** (totals plus per-PID fault and block histograms). **`Simulation.print_system_analysis`** summarizes “who faulted most”, “who blocked most”, and “which path had file-lock contention”. Phase 1’s table still answers **which policy used fewer context switches** on the same workload.

---

## 13. Final integrated demo explanation

**`Simulation.run_final_demo()`** (run with `python main.py --final` or `--phase6`) stitches together: **producer–consumer** traffic, **priority inversion** on a sensor mutex with **inheritance**, **blocking file append** plus **file-lock contention** on a shared path, **paging** under a small frame pool, **aging**, and ends with **grouped timeline + metrics + static FIFO vs RR vs Priority comparison text**.

---

## 14. Known limitations

- **Not cycle-accurate** or trace-driven; ticks are abstract.
- **No preemption inside “syscall scripts”**—demo steps are ordered for clarity.
- **Global FIFO eviction** is not LRU or working-set aware.
- **Single CPU**; no SMP, no IRQ model beyond narration.

---

## 15. Future improvements

- **Per-process page replacement** or **LRU approximations** for more realistic memory pressure studies.
- **I/O schedulers** (priority inheritance across file wait queues).
- **Optional RNG mode** behind a seed for stochastic stress (off by default).
- **Richer syscall surface** (select, timeouts) still without OS threads.

---

### Why BLOCKED is central

**BLOCKED** is the hinge between **resource waiting** and **scheduling**: it removes a process from meaningful CPU competition, models **backpressure** (buffer full, disk slow, page not resident), and makes **inversion** visible. The tick loop **keeps advancing** while work is outstanding (fault timers, I/O timers), which matches real kernels that stay busy while threads wait.

### Why deterministic simulation

Determinism makes **grading, regression tests, and live demos** predictable. The project intentionally **simplifies realism** (e.g., fixed I/O latency, scripted handoffs) to keep causality easy to explain in a capstone defense.

### Where to look in code

| Topic | Location |
|-------|-----------|
| Timeline / metrics / analysis | `os_core/observability.py`, `Simulation` helpers |
| Final demo | `Simulation.run_final_demo` in `os_core/simulation.py` |
| Entry point | `main.py` (`--final` / `--phase6`) |
| Diagrams | `ARCHITECTURE_DIAGRAMS.md` |
