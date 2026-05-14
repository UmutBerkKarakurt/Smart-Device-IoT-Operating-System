# Architecture Diagrams — Mini IoT OS Simulator

Subsystem relationships (educational / presentation). The real control flow is implemented in `os_core/simulation.py`.

## ASCII — data and control flow

```
                    +----------------+
                    |  Process table |
                    | (PCB / states) |
                    +-------+--------+
                            |
                            v
+----------------+   +-------+--------+   +------------------+
| MemoryManager  |<--|   Scheduler    |-->| FileSystem       |
| (frames,       |   | (FIFO/RR/Pri)  |   | (dirs, locks,    |
|  page faults)  |   +-------+--------+   |  I/O latency)    |
+----------------+           |          +---------+----------+
                             |                    |
                             v                    v
                    +--------+---------+  +-------+--------+
                    | SyncPorts /      |  | FileSystemPorts|
                    | Mutex, Semaphore |  | block/wake      |
                    +------------------+  +-----------------+
                             |
                             v
                    +------------------+
                    | Simulation clock |
                    | (deterministic)   |
                    +------------------+
```

## Mermaid — component interaction

```mermaid
flowchart TB
  subgraph Processes
    PCB[PCB table / states]
  end
  subgraph Scheduling
    SCH[Scheduler FIFO or RR or Priority]
  end
  subgraph Memory
    MM[MemoryManager paging and faults]
  end
  subgraph Filesystem
    FS[FileSystem paths and exclusive locks]
  end
  subgraph Concurrency
    SYNC[Locks Semaphores SharedBuffer]
  end
  SIM[Simulation tick loop]
  PCB --> SCH
  SCH --> SIM
  SIM --> MM
  SIM --> FS
  SIM --> SYNC
  MM -. page-in complete .-> SCH
  FS -. I/O done wake .-> SCH
  SYNC -. wake on signal release .-> SCH
```

## Mermaid — scheduler cross-links

```mermaid
flowchart LR
  SCH[Scheduler]
  PM[Process manager]
  MM[Memory manager]
  FS[File system]
  PR[Sync primitives]
  SCH <--> PM
  SCH --> MM
  SCH --> FS
  SCH --> PR
  MM <--> FS
```

The scheduler does not parse file contents or page tables directly; `Simulation._cpu_step` orchestrates: pick a runnable PCB, optionally perform a logical memory access (possibly faulting), decrement burst, and coordinate blocking for faults, file I/O, and synchronization through shared `block_process` / `wake_process` ports.
