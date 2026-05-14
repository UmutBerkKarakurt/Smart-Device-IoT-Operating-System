# Mini OS Simulator (Phase 0)

Educational **simulator** (not a real kernel) that models core operating system ideas with clear logs and a modular layout. Phase 0 adds the project skeleton: PCB, process manager, FIFO scheduler, memory accounting, in-memory file store, a simple lock, and a scripted demo.

## Run the demo

From this directory (`mini_os_simulator/`):

```bash
python main.py
```

## Run tests

```bash
pip install -r requirements.txt
pytest
```

## Layout

| Path | Role |
|------|------|
| `main.py` | Runs the Phase 0 demo |
| `os_core/` | Logging + simulation wiring |
| `process/` | PCB, process manager, FIFO scheduler |
| `memory/` | Total/used memory and per-process reservations |
| `filesystem/` | Dictionary-backed files |
| `concurrency/` | Mutex-style lock skeleton |
| `tests/` | Pytest smoke tests |

Import `Simulation` from `os_core.simulation` (not from `os_core` package root) to avoid circular imports. See `PHASE_0_REPORT.md` for design notes and course-report material.
