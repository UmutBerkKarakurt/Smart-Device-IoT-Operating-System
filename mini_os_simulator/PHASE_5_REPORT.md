# Phase 5 Report — Priority Inversion, Priority Scheduling, Inheritance, Aging, Stress, Metrics

## 1. Effective priority and static `priority`

Each `PCB` keeps the **static** `priority` field from earlier phases (used for IoT semantics and as the base for scheduling). Phase 5 adds **`aging_bonus`** and **`mutex_inheritance_floor`**, and exposes **`effective_priority`** as a read-only property:

`effective_priority = max(priority + aging_bonus, mutex_inheritance_floor)`.

The **PriorityScheduler** orders only on **`effective_priority`**, never on the raw `priority` alone, so starvation mitigation and mutex inheritance can change who runs next without rewriting process records.

## 2. `PriorityScheduler` and `create_scheduler`

`process/scheduler.py` still contains the original **FIFO** `Scheduler` and **`RoundRobinScheduler`** unchanged in behavior. A new **`PriorityScheduler`** subclasses `Scheduler`, reuses the same `enqueue` / ready `deque`, and overrides **`pick_next`**: it scans the deque for **READY** PCBs (skipping **BLOCKED** and **TERMINATED** like the baseline), selects the highest **`effective_priority`**, and re-enqueues the others in their original relative order.

**Tie-break (documented):** if two READY processes share the same `effective_priority`, the scheduler prefers the **smaller `arrival_time`** (FIFO among equals), then the **smaller `pid`**, then earlier position in the ready scan.

The factory **`create_scheduler("fifo" | "rr" | "priority", quantum=...)`** adds **`policy="priority"`** without altering FIFO/RR branches.

**Log line (required shape):**  
`[Scheduler] PriorityScheduler selected pid=X priority=Y`  
where `Y` is **`effective_priority`**.

## 3. Why priority inversion happens here (explicit)

**Priority inversion** is not “LOW runs before HIGH.” It is: **HIGH is blocked on a resource owned by LOW**, while **another runnable task (MEDIUM)** has priority **between** LOW and HIGH. The CPU scheduler only sees **READY** processes; **BLOCKED** HIGH does not compete. MEDIUM then receives the CPU repeatedly, so **LOW never runs long enough to release the mutex**, and **HIGH waits indirectly**—classic indirect blocking / inversion chain.

In `run_phase5_demo()` Part 1: **DataLogger** (LOW, `priority=1`) holds **`sensor_lock`** before any scheduling tick; **AlarmMonitor** (HIGH, `priority=3`) attempts the same lock and blocks; **NetworkSync** (MEDIUM, `priority=2`) remains **READY** and wins scheduling over LOW **when inheritance is off**, so LOW’s critical section does not progress.

## 4. The mandatory medium-priority role

**NetworkSync** exists to be the **“middle”** priority: higher than the lock holder, lower than the alarm. Without inheritance, **`effective_priority`** reduces to **`priority + aging_bonus`** for everyone except the mutex holder’s floor; MEDIUM’s base (2) beats LOW’s base (1), so MEDIUM **steals the CPU** while HIGH is stuck on the wait queue. That is the pedagogical purpose of the medium task.

## 5. Priority inheritance (mandatory)

When a process blocks in **`Lock.acquire`** with **`SyncPorts`** wired for Phase 5, **`recompute_mutex_inheritance(lock)`** runs. It sets the **current holder’s** `mutex_inheritance_floor` to the maximum of **`waiter.priority + waiter.aging_bonus`** over all PIDs in **`waiting_queue`** (blocked waiters only). That floor is **not** additive with the waiters’ own inheritance floors (avoids recursion).

**Scope (precise):** the floor applies **while this lock is held** and **while there is at least one waiter**; when the wait queue becomes empty, the holder’s floor for this lock becomes **0**. When the holder **releases** the mutex, **`clear_mutex_inheritance_for_holder(releaser_pid)`** clears the releaser’s floor; the next owner is woken and **`recompute_mutex_inheritance`** runs again for any **remaining** waiters.

**Logs:** inheritance boosts and clears are emitted under **`[Concurrency]`** (e.g. `priority inheritance -> holder pid=…` and `mutex inheritance restored base scheduling weight …`).

## 6. Why inheritance fixes the inversion (explicit)

Inheritance raises the **holder’s** `mutex_inheritance_floor` to at least the **blocked high-priority waiter’s** base+aging, so the holder’s **`effective_priority`** can jump **above MEDIUM’s**. The **PriorityScheduler** then chooses the holder, who runs its critical section, **releases** the mutex, and unblocks HIGH. Without inheritance, the scheduler never “sees” HIGH’s urgency in LOW’s scheduling weight, so MEDIUM can starve LOW indefinitely (until aging or scripted release).

## 7. Starvation prevention — aging (mandatory)

**Aging** is **deterministic**: at the **start** of each **`_cpu_step`**, if **`priority_aging_enabled`**, the scheduler is a **`PriorityScheduler`**, and **`sim_clock % aging_interval == 0`** (with `sim_clock > 0`), every PCB in **`ProcessState.READY`** receives **`aging_bonus += 1`**. That increases **`priority + aging_bonus`** and therefore **`effective_priority`** whenever the inheritance floor does not already dominate.

**Why aging is still needed (explicit):** two READY tasks can have **parallel base priorities** that never invert (e.g. repeated tie or long-lived lower-priority workload). Aging slowly increases the **starving** READY process’s weight so it eventually competes—**without randomness**, only global tick parity.

**Log line (required shape):**  
`[Scheduler] Aging applied pid=X effective_priority=Y`

**Default:** `Simulation.aging_interval = 5`; stress uses `4` for a shorter deterministic cycle.

## 8. Stress / failure scenario and metrics

**`run_phase5_demo()` Part 3** builds two **fresh** `Simulation` instances (same script, no threads, no RNG): one with **`mutex_inheritance_enabled=False`**, one with **`True`**. Both use **`PriorityScheduler`**, **`priority_aging_enabled=True`**, **mixed priorities**, **lock contention** (HIGH blocks on LOW’s mutex), and a **fixed tick budget** before a **scripted release**.

A **printed comparison table** includes: **context switches**, **final sim clock**, **first CPU dispatch tick** (`PCB.start_time`) for Alarm / DataLogger / Network when present, and **remaining CPU burst** for DataLogger and Network **captured immediately before** the scripted `release` (proxy for “how much did the holder / medium steal before the lock was released?”).

## 9. Integration with `Simulation._cpu_step`

No rewrite of FIFO/RR internals. **`PriorityScheduler.pick_next`** cooperates with the existing loop: **page faults**, **file I/O blocking**, and **sync block/wake** still use **`_sync_block_process` / `_sync_wake_process`**. **Aging** runs at the **beginning** of **`_cpu_step`** so it observes PCBs that have already returned to **READY** after a quantum expiry. **Global clock** bumps remain in **`_advance_sim_clock`** (including the idle fault/I/O path).

## 10. Lock and `SyncPorts` changes

**`concurrency/locks.py`:** after **`block_process`** on contention, **`recompute_mutex_inheritance(self)`** is invoked if present on ports. On **`release`**, **`clear_mutex_inheritance_for_holder(pid)`** runs for the releaser, then ownership may pass to the FIFO waiter; **`recompute_mutex_inheritance(self)`** runs for the new holder if ports support it.

**`concurrency/sync_ports.py`:** optional **`recompute_mutex_inheritance`** and **`clear_mutex_inheritance_for_holder`**. Phase 3 tests keep **`SyncPorts(block, wake, log)`** only—callbacks default to **`None`**, so behavior is unchanged.

## 11. Tests (`tests/test_phase5_priority.py`)

Deterministic pytest coverage includes:

- **Priority ordering** among READY PCBs by `effective_priority`.
- **Tie-break** by `arrival_time` then `pid`.
- **Inheritance** raises the holder’s floor when a high-priority process waits.
- **Restoration** on release (floor cleared for releaser path).
- **Aging** increases `aging_bonus` / `effective_priority` on a fixed tick schedule.
- **Inversion mitigation:** after 12 fixed CPU ticks, **DataLogger** (`LOW` holder) has **lower** `remaining_time` **with inheritance than without** (fixed script, same burst sizes).

## 12. `run_phase5_demo()` and `main.py`

**`run_phase5_demo()`** narrates: **Part 1** inversion with inheritance off; **Part 2** same with inheritance on (snapshots of `remaining_time`); **Part 3** stress metrics table (inheritance off vs on).

**`main.py`:** default run is **Phase 5**. Flags **`--phase0`** … **`--phase4`** select earlier demos (Phase 4 requires **`--phase4`** explicitly).

## 13. Simplified realism, trade-offs, limitations, example logs, file manifest

**Simplified realism:** one mutex inheritance model (floor from waiters’ `priority + aging_bonus` only), **no PRIO_INHERIT chain across multiple locks per PCB accounting**, no **priority ceilings** protocol, no **RQ mutex** migration. Aging is global tick-modulo, not per-queue wait-time thresholds.

**Trade-offs:** inheritance can **over-boost** if many high waiters exist; aging can reorder **all** READY workloads on the same interval, not only the starved subset.

**Limitations:** no **random** workloads; **no Python threads**; inheritance **requires** `Simulation.mutex_inheritance_enabled` and **`SyncPorts`** wiring (raw `Lock` without ports unchanged for Phase 0-style uses).

**Example log lines (abbreviated):**  
`[Scheduler] PriorityScheduler selected pid=1 priority=3`  
`[Concurrency] sensor_lock: priority inheritance -> holder pid=1 inherits floor=3 (was 0)`  
`[Scheduler] Aging applied pid=1 effective_priority=9`  
`[Concurrency] mutex inheritance restored base scheduling weight for pid=1 (cleared inheritance floor was 3)`

**Files changed / added**

| Action | Path |
|--------|------|
| Extended | `process/pcb.py` |
| Extended | `process/scheduler.py` |
| Extended | `process/__init__.py` |
| Extended | `concurrency/sync_ports.py` |
| Extended | `concurrency/locks.py` |
| Extended | `os_core/simulation.py` |
| Extended | `main.py` |
| Added | `tests/test_phase5_priority.py` |
| Added | `PHASE_5_REPORT.md` |

**Test count:** `31` tests under `tests/` (`python -m pytest tests/ -q`).
