# Phase 1 Report — Mini OS Simulator (Smart Device / IoT)

## 1. What was implemented

- **FIFO baseline preserved**: `Scheduler` in `process/scheduler.py` keeps the same ready-queue `enqueue` / `pick_next` behavior as Phase 0.
- **Round Robin**: `RoundRobinScheduler` subclasses `Scheduler`, adds a configurable **time quantum** (ticks). The **CPU simulator** in `Simulation._cpu_step` runs up to `quantum` consecutive ticks on the same process before re-enqueueing it if it still has `remaining_time`.
- **Policy selection**: `create_scheduler("fifo" | "rr", quantum=...)` factory returns the appropriate scheduler object; the simulator reads `getattr(scheduler, "quantum", 1)` so FIFO implicitly uses quantum **1** without adding a field to `Scheduler`.
- **PCB metrics** (abstract ticks): `arrival_time`, `start_time`, `completion_time`, `waiting_time`, `turnaround_time`, `response_time`, with `on_first_cpu_dispatch` and `finalize_metrics`.
- **Phase 1 demo**: `run_phase1_demo()` builds the same IoT-named workload twice (FIFO then RR), prints and logs a comparison table including **context switch** counts.
- **Tests**: `tests/test_phase1_scheduling.py` covers FIFO order, RR completion/re-enqueue, metric sanity, and context-switch growth.

## 2. Files changed / added

| Path | Role |
|------|------|
| `process/pcb.py` | Extended PCB + metric helpers |
| `process/process_manager.py` | `arrival_time` on `create_process` |
| `process/scheduler.py` | `RoundRobinScheduler`, `create_scheduler`, `PolicyName` |
| `process/__init__.py` | Exports for RR + factory |
| `os_core/simulation.py` | Unified `_cpu_step`, Phase 1 demo, stats |
| `main.py` | Default Phase 1; `--phase0` for Phase 0 only |
| `tests/test_phase1_scheduling.py` | **New** Phase 1 tests |
| `PHASE_1_REPORT.md` | **New** this document |

## 3. FIFO baseline explanation

The **FIFO scheduler** is a strict ready queue: processes are appended at `enqueue` time and `pick_next` always takes the head. In this project, **FIFO mode in the simulator** pairs that queue with an implicit **quantum of 1 tick**: each dispatch runs exactly one tick of CPU, then unfinished work is pushed to the tail of the queue. That matches the Phase 0 tick granularity while keeping the `Scheduler` class itself unchanged.

## 4. Round Robin (enhanced) explanation

**RR** uses the **same** ready queue as FIFO. The difference is the **quantum**: a process may run for up to `quantum` consecutive ticks before being marked `READY` and re-enqueued if `remaining_time > 0`. Each tick decrements `remaining_time` and advances the simulation clock. When `remaining_time` reaches zero, the process is terminated and memory is released, with metrics finalized at that clock.

## 5. Why RR improves fairness vs FIFO (this simulator’s definitions)

With **FIFO + quantum 1**, every tick forces a rotation through the ready queue, which is fair in the long run but creates **many** dispatches and tends to spread **response** favorably (short jobs get frequent turns) while still paying high **context-switch** cost.

With a **larger RR quantum** (e.g. 3), a long job like **CameraProcess** holds the CPU for several ticks in a row, so **average waiting** and **average turnaround** for the mix can drop because long jobs finish with fewer total rotations, at the cost of **worse average response** for peers that must wait behind a longer slice.

## 6. Trade-off: fairness / responsiveness vs context-switch overhead

Smaller quantum improves **responsiveness** and interactivity (every process gets CPU often) but increases **context switches** and scheduler overhead. Larger quantum reduces **context switches** and can improve **throughput-like** metrics for CPU-bound bursts in a toy workload, but short jobs may wait longer at the tail of a slice—**fairness in time-to-first-response** suffers.

## 7. Metrics definitions

All times are **integer simulation ticks**.

| Metric | Definition |
|--------|------------|
| **Arrival** | `arrival_time` set when the process is created (Phase 1: same clock for all jobs in the batch). |
| **Start** | `start_time`: clock value at the **first** tick the process begins executing on the CPU (`on_first_cpu_dispatch`). |
| **Completion** | `completion_time`: clock value **after** the last tick that drives `remaining_time` to 0 (`finalize_metrics`). |
| **Turnaround** | `completion_time - arrival_time` |
| **Response** | `start_time - arrival_time` |
| **Waiting** | `turnaround_time - cpu_burst_time` (for completed jobs, equals time not executing the CPU burst after arrival). |

## 8. Baseline vs enhanced comparison (representative run)

Example output from `python main.py` on this codebase (5 IoT processes, total burst 31 ticks, RR `quantum=3`):

| Policy | Avg Waiting | Avg Turnaround | Avg Response | Context Switches |
|--------|-------------:|---------------:|-------------:|------------------:|
| FIFO (q=1) | 13.20 | 19.40 | 2.00 | 28 |
| RR (q=3) | 11.00 | 17.20 | 5.20 | 11 |

## 9. IoT theme explanation

Phase 1 uses **device-shaped** process names and burst sizes: **AlarmProcess** (short), **TemperatureSensorProcess** (small), **CameraProcess** (long CPU), **DataLoggerProcess** (medium), **NetworkSyncProcess** (medium/long). **Priority** is set for realism but **does not** influence scheduling in Phase 1.

## 10. Known limitations

- **No true blocking I/O**, paging, priority scheduling, or deadlock scenarios—only CPU + ready queue + memory accounting hooks.
- **Metrics** are undefined for processes **killed** without running to `remaining_time == 0` (e.g. Phase 0’s manual `terminate` on the long logger).
- **Idle clock**: if the CPU is idle, the simulation clock does not advance (same as Phase 0’s “stop when empty” behavior).

## 11. Phase 2 suggestions

- Priority scheduling (still avoiding starvation with aging).
- I/O wait queues and **BLOCKED** → **READY** transitions.
- Multi-level feedback queue combining **response**, **fairness**, and **switch** cost.
- Energy-aware scheduling for IoT (longer quanta when on battery, shorter when interactive).

---

## Context switch counting (explicit)

A **context switch** is counted whenever a **new CPU slice** begins for process **B** and the previously dispatched-on-CPU process **A** satisfies `A != B`, including **idle → first process** after the CPU was cleared (`_cs_prev_pid` reset on completion). Continuing the **same** process across multiple ticks inside one RR slice does **not** add switches.

## Design justification (FIFO baseline, RR enhanced, trade-off)

We kept **`Scheduler`** as the **FIFO baseline** so Phase 0 and tests that assert queue ordering remain valid. RR is introduced as a **subclass plus factory** so policy is chosen without rewriting the process table or memory subsystem. The **quantum** is enforced in **`Simulation._cpu_step`**, shared by FIFO (implicit quantum 1) and RR (explicit `RoundRobinScheduler.quantum`), which avoids duplicating queue logic and keeps logging and metrics in one place. The **trade-off** is intentional: FIFO-style quantum-1 minimizes **per-job wait spikes** from long jobs but maximizes **context switches**; RR with a larger quantum batches CPU work, cutting **switches** and often **average waiting/turnaround** in bursty IoT mixes while **raising average response** because peers wait longer between dispatches.
