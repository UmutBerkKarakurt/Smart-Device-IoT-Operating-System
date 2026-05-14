# Phase 4 Report — File System and I/O Interaction

## 1. Hierarchical in-memory file system

`FileSystem` (`filesystem/file_system.py`) now models **directories** and **absolute paths** (`normalize_path` collapses `//`, applies leading `/`, rejects `..` segments safely). Operations include **`mkdir`**, **`list_dir`**, **`create`**, **`read`**, **`write`**, **`append`**, and **`delete`**. Example paths used in the demo and tests include `/logs/temperature.log`, `/logs/camera.log`, `/config/device.cfg`, and `/data/network_sync.txt`. There is **no persistence** and **no real disk**; all state lives in Python structures.

## 2. File metadata (`FileMetadata`)

Each regular file is backed by an inode-style record with a **`FileMetadata`** dataclass: **`name`**, **`path`**, **`size`**, **`created_at`** and **`modified_at`** (simulation ticks via `set_simulation_tick`), optional **`owner_pid`**, **`open_count`**, and **`locked_by`** (mirrors the live lock owner for presentation). `get_metadata` logs under **`[FileSystem]`** for inspection and tests.

## 3. Open file table and PCB extension

`Simulation` maintains a global **`_open_by_fd`** map of **`OpenFileDescriptor`** entries (`fd`, `pid`, `path`, `mode`, `offset` reserved for future seek semantics). **`open_file`** allocates an fd, increments inode **`open_count`**, and appends **`(fd, path, mode)`** tuples to **`PCB.opened_files`** (replacing the old plain string list). **`close_file`** reverses those updates. Open/close lines are logged under **`[Process]`** so they read as process-centric actions.

## 4. Blocking file I/O (simulated device latency)

Disk-like operations that should cost time are modeled with **`PendingFileIoJob`** objects: fixed **`io_ticks`** (default **`Simulation.file_io_ticks`**, typically `3`). **`file_blocking_read` / `write` / `append` / `delete`** first take an **exclusive file lock** (see §6), enqueue a job, emit **`[Simulation]`** “I/O queued” plus **`[Scheduler]`** “blocked for I/O”, then call **`_sync_block_process`** so the PCB moves **RUNNING/READY → BLOCKED** and the simulated CPU slot is cleared when applicable. **`_advance_file_io_timers`** runs at the start of every **`_cpu_step`**, alongside page-fault timers. When a job’s counter reaches zero, **`_complete_file_io`** applies the deferred mutation (writes/appends/deletes hit the tree only here; reads are latency-only), **`release_exclusive_lock`**, **`_sync_wake_process`** (**BLOCKED → READY** + enqueue), and logs **I/O complete** / **wake** under **`[Scheduler]`**.

## 5. Scheduler interaction and idle time advancement

**FIFO** and **Round Robin** cores are unchanged. **`pick_next`** still **skips BLOCKED** PIDs and drops them from the head of the deque without re-queueing. While every READY process is blocked on I/O or page faults, **`_cpu_step`** advances the global clock when **`pending_fault_jobs()`** or **`_pending_file_io`** is non-empty so I/O and faults still make progress—mirroring the existing fault-only idle path, extended for file I/O. **`_phase3_cpu_ticks`** was updated to continue the tick loop when file I/O is pending, not only page faults.

## 6. File-level locking (exclusive)

Each file inode carries **`lock_owner`** and a **FIFO `lock_waiters`** deque. **`try_acquire_exclusive_lock`** grants immediately when free or when the caller already holds the lock; otherwise it logs **contention**, appends the waiter, and (when **`FileSystemPorts`** is wired) calls **`block_process`**. **`release_exclusive_lock`** clears the owner, hands the lock to the next waiter if any (logging **grant from wait queue**), and issues **`wake_process`** through ports. This is deliberately simpler than POSIX `flock`/`fcntl` shared/exclusive modes: **one writer-style exclusive lock** blocks all other readers and writers until release, which matches the coursework narrative “writer holds the file; others wait.”

## 7. `run_phase4_demo()`

The demo seeds **`/logs`**, **`/config`**, **`/data`**, creates the four canonical files, writes a small **`/config/device.cfg`**, then instantiates **TemperatureSensorProcess**, **CameraProcess**, **DataLoggerProcess**, and **NetworkSyncProcess** with RR **`quantum=2`**. It performs a **temperature log blocking write** while other READY processes still receive CPU time, a **camera log write** on a separate path, a **DataLogger append** to **`/data/network_sync.txt`**, and a **NetworkSync read** that first hits **lock contention** while the logger still holds the lock during its own I/O. After enough ticks, the second read call queues read I/O. **`list_dir('/logs')`** is asserted for deterministic output, then **`_run_until_all_terminated`** drains bursts.

## 8. `main.py` entry point

The default run is **Phase 4**. Flags **`--phase0`**, **`--phase1`**, **`--phase2`**, and **`--phase3`** remain available for earlier narratives.

## 9. Logging map

| Topic | Typical component |
|--------|-------------------|
| mkdir / create / rw / append / delete / metadata / locks | `[FileSystem]` |
| open / close / state transitions | `[Process]` |
| enqueue, pick_next, quantum, I/O block/complete/wake, lock wait messages | `[Scheduler]` |
| I/O job queueing | `[Simulation]` |

## 10. Tests (`tests/test_phase4_filesystem.py`) and file manifest

Deterministic pytest coverage includes: **path normalization**, **mkdir + list_dir + append**, **open/close fd + PCB `opened_files`**, **I/O blocking with completion and cross-process CPU progress**, **scheduler skip of BLOCKED PIDs**, **exclusive lock FIFO handoff**, and **metadata timestamps**. **`tests/test_basic_simulation.py`** still smoke-tests **`FileSystem`** with absolute **`/x.txt`** paths.

**Files changed / added**

| Action | Path |
|--------|------|
| Rewritten / extended | `filesystem/file_system.py` |
| Added | `filesystem/file_system_ports.py` |
| Extended | `filesystem/__init__.py` |
| Extended | `os_core/simulation.py` |
| Extended | `main.py` |
| Extended | `process/pcb.py` |
| Extended | `process/process_manager.py` |
| Extended | `tests/test_basic_simulation.py` |
| Added | `tests/test_phase4_filesystem.py` |
| Added | `PHASE_4_REPORT.md` |

**Test count:** `25` tests under `tests/` (`python -m pytest tests/ -q`).

Real storage devices (eMMC, SD, NAND) expose **latency** and **queueing** that are unrelated to CPU speed. The simulator cannot perform real I/O, so **fixed `io_ticks`** stand in for “media service time.” Blocking the issuing process forces the **scheduler** to expose **READY/BLOCKED** dynamics: other work proceeds while one PCB waits, and **wake + enqueue** makes completion visible on the same global tick clock used for paging and semaphores.

## 12. Why an open file table (explicit)

Operating systems track **per-process descriptors** separately from on-disk inode state so they can enforce **per-fd mode/offset**, **refcounted closes**, and **revoke access on exit** without corrupting the namespace. Here, **`open_file` / `close_file`** mirror that separation: the in-memory tree stores bytes; the **OFT** plus **`PCB.opened_files`** ties descriptors to owners for future extensions (seek pointers, dup, unbuffered flags) without overloading the inode alone.

## 13. Why file locks, scheduling connection, realism, trade-offs, example logs, limitations, Phase 5 ideas

**Why file locks:** concurrent IoT workloads (logger + uploader + camera) contend on shared paths. A single **exclusive lock** gives a deterministic story: one PID holds the file across its critical section and simulated I/O, others **block in FIFO order**—the same pedagogical pattern as the mutex in Phase 3.

**Connection to scheduling:** locks and I/O both funnel through **`SyncPorts`-style `FileSystemPorts`** with **`_sync_block_process` / `_sync_wake_process`**, so **BLOCKED** processes never consume quanta, and **`pick_next`** naturally rotates among remaining READY PIDs.

**Simplified realism:** one global timer list, no elevator algorithm, no read/write lock split, no async cancellation, no partial writes.

**Trade-offs:** exclusive locking is **pessimistic** for read-heavy sharing; deferred writes mean **`read` cannot observe uncommitted data** (there is no buffer cache flush model); **`open` is instantaneous** to keep Phase 0–3 complexity bounded.

**Example log lines (abbreviated):**  
`[Simulation] I/O queued pid=1 op=write path='/logs/temperature.log' io_ticks=3`  
`[Scheduler] pid=1 blocked for I/O (3 ticks) op=write path='/logs/temperature.log'`  
`[Scheduler] pid=1 I/O complete op=write path='/logs/temperature.log'`  
`[FileSystem] lock contention '/data/network_sync.txt': holder pid=3 waiter pid=4`  
`[Scheduler] Picked next pid=2 name='CameraProcess'` (while others are blocked)

**Limitations:** no **symlinks**, **permissions**, **working directory** `chdir`, **sparse files**, **directories as fds**, or **per-fd seek** beyond the stored offset field; delete refuses busy files with positive **`open_count`**; lock state is not persisted across `Simulation` instances.

**Phase 5 ideas:** **read–write shared locks**, **directory locks**, **record locking**, **journal / WAL** with commit ticks, **flash wear** counters, **VFS layer** mounting multiple `FileSystem` instances, and tying **`program_counter`** to a tiny **syscall bytecode** so file ops trigger automatically instead of scripted calls.
