# Phase 3 Report — Concurrency and Synchronization

## 1. Mutex (`Lock` in `concurrency/locks.py`)

The mutex was extended from a simple owner field to a **FIFO wait queue** of process IDs (`collections.deque`). `acquire(pid, ports)` grants the lock immediately when free or when the caller already holds it (non-reentrant across different primitives, but the same PID short-circuits as before for teaching clarity). When the lock is held by another PID and a **`SyncPorts`** bundle is supplied, the contender is appended to `waiting_queue`, logged as **contention**, optional `log_scheduler` emits a scheduler-facing line, and `ports.block_process(pid)` transitions **RUNNING or READY → BLOCKED** (wired from `Simulation`). Without `ports`, behavior matches the Phase 0 skeleton: acquisition fails with a log line and no queueing.

`release(pid, ports)` validates the owner. On success, if the wait queue is non-empty, the next PID becomes owner **before** `wake_process` runs (ownership transfer is logged), then `ports.wake_process` moves that process to **READY** and **enqueues** it. This matches a Mesa-style “signal” where the awakened process must run under the lock already granted by the monitor implementation.

## 2. Counting semaphore (`CountingSemaphore` in `concurrency/semaphore.py`)

A **counting semaphore** exposes `wait(pid, ports)` and `signal(pid, ports)` (Dijkstra **P** / **V** naming is documented here; code uses `wait` / `signal`). `count` starts at `initial`. **`wait`**: if the PID has a **post-signal handoff** pending (see below), the wait completes without changing `count`. Else if `count > 0`, decrement and succeed. Else enqueue the PID (FIFO), log, and block via `ports`.

**`signal` semantics (documented choice):** If the FIFO wait queue is non-empty, one waiter receives a **direct handoff**: the PID is recorded in `_pending_grants`, the waiter is woken, and **`count` is not incremented** (the `V` operation absorbed the blocked `P`). If there are no waiters, `count` is incremented. The next `wait` from that PID observes the pending grant and returns success immediately. This avoids the classic bug where a woken process retries `wait` while `count` is still zero.

## 3. Producer–consumer and shared buffer

**`SharedBuffer`** (`concurrency/shared_buffer.py`) is a fixed-capacity deque of items. `produce` rejects when full; `consume` rejects when empty. Operations log under **`[Concurrency]`** with the buffer name and current size.

The **Phase 3 demo** (`Simulation.run_phase3_demo`) wires a **bounded buffer** with:

- **`empty_slots`**: initial `count = capacity` (empty slots available).
- **`full_slots`**: initial `count = 0` (no full slots yet).
- **`buffer_mutex`**: protects the critical section around `SharedBuffer` mutations.

**Ordering** (deadlock-avoiding): producer does `empty.wait` → `mutex.acquire` → `produce` → `mutex.release` → `full.signal`. Consumer does `full.wait` → `mutex.acquire` → `consume` → `mutex.release` → `empty.signal`.

**IoT roles:** `CameraProcess` (producer), `DataLoggerProcess` (consumer), and `TemperatureSensorProcess` (extra READY workload). The demo is **fully scripted**: explicit calls into produce/consume helpers and fixed `_phase3_cpu_ticks` counts, not random I/O.

## 4. Scheduler interaction

**FIFO and Round Robin** factories are unchanged (`process/scheduler.py`). `pick_next` already **skips BLOCKED** (and TERMINATED) PIDs; when a blocked PID is at the head of the ready queue it is **popped and discarded** so it does not starve the queue. `Simulation._sync_wake_process` re-**enqueues** unblocked processes.

Phase 3 adds **`_sync_block_process`** / **`_sync_wake_process`** and **`_make_sync_ports`**: blocking clears **`_current_pcb`** when the blocked PID is the simulated CPU holder, mirroring the page-fault path so the next tick can dispatch another READY process. The Phase 3 demo sets **`enable_memory_access = False`** so paging and `MemoryPorts` behavior from Phase 2 remain unchanged when that flag is used elsewhere.

## 5. Wake lifecycle

1. A process blocks on a mutex or semaphore: state becomes **BLOCKED**, optional scheduler log, and if it held the CPU slot, **`_current_pcb`** is cleared.
2. Another process runs (scripted CPU ticks) or a partner runs a **signal** / **release**.
3. **`wake_process`**: **BLOCKED → READY**, then **`scheduler.enqueue`**. Duplicate enqueue is ignored by the scheduler.
4. On the next `pick_next`, the process competes with other READY PIDs. For the mutex, **`release`** may grant ownership **synchronously** to the next waiter while still issuing **`wake_process`** so it becomes schedulable for CPU work after leaving the critical section.

## 6. Why no Python `threading`

The simulator advances a **single global clock** and explicit **ticks**. Real `threading` locks would introduce **nondeterministic interleaving** and hide the pedagogical mapping from “BLOCKED in the PCB” to “wait queue on the primitive.” All synchronization is therefore **simulated**: primitives call into **`SyncPorts`** callbacks implemented on **`Simulation`**, without importing `Simulation` from `concurrency/` (small port type in `concurrency/sync_ports.py`, analogous to Phase 2 **`MemoryPorts`**).

## 7. Trade-offs

- **Handoff set on semaphores** adds a small amount of state but keeps **`wait` idempotent** after wake without incrementing `count` twice.
- **Mutex + two semaphores** duplicate structure that a single monitor could express; the split matches classic OS coursework and keeps each primitive testable in isolation.
- **Scripted demo** maximizes log clarity; it does not parse user programs or automate “when a PCB hits instruction N.”
- **`SyncPorts.log_scheduler`** is optional; when present, contention lines also appear under **`[Scheduler]`** for a single timeline.

## 8. Example logs

From `python main.py` (default Phase 3):

- `[Simulation] === Phase 3 demo: mutex + semaphores + bounded buffer (scripted) ===`
- `[Concurrency] empty_slots: wait blocked pid=1 count=0 queue=[1]`
- `[Scheduler] pick_next: skip blocked pid=1`
- `[Scheduler] Picked next pid=3 name='TemperatureSensorProcess'`
- `[Concurrency] empty_slots: signal by pid=2 handoff to pid=1 (remaining_waiters=[] count=0)`
- `[Concurrency] empty_slots: wait resumed pid=1 (post-signal handoff) count=0`
- `[Concurrency] iot_frame_buffer: produce item='frame-C' size=2/2`

## 9. Known limitations

- No **priority inheritance** or **deadlock detection**.
- **Reentrancy** for the mutex is only “same PID may acquire again”; there is no depth counter.
- **Starvation** is possible in theory if the scheduler policy and workload conspired; the demo uses a fixed script to avoid that narrative.
- Synchronization is **not** automatically invoked from `program_counter`; demos call primitives explicitly.

## 10. Phase 4 ideas

- **Condition variables** or a **monitor** wrapper built on this mutex and a predicate.
- **Timeouts** on `wait` / `acquire` in simulation ticks.
- **Priority scheduling** with **priority ceiling** or **inheritance** for IoT scenarios.
- **Deadlock** injection and detection exercises.
- Deeper **integration** with `remaining_time` so each “instruction” of a synthetic bytecode drives `wait`/`signal`.

## 11. Files changed / added

| Action | Path |
|--------|------|
| Added | `concurrency/sync_ports.py` |
| Extended | `concurrency/locks.py` |
| Added | `concurrency/semaphore.py` |
| Added | `concurrency/shared_buffer.py` |
| Extended | `concurrency/__init__.py` |
| Extended | `os_core/simulation.py` |
| Extended | `main.py` |
| Added | `tests/test_phase3_sync.py` |
| Added | `PHASE_3_REPORT.md` |
