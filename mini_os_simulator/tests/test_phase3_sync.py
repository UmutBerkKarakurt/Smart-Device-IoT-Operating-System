"""Phase 3: mutex wait queue, semaphore handoff, shared buffer, scheduler skips BLOCKED."""

from __future__ import annotations

from typing import List

from concurrency.locks import Lock
from concurrency.semaphore import CountingSemaphore
from concurrency.shared_buffer import SharedBuffer
from concurrency.sync_ports import SyncPorts
from process.pcb import ProcessState
from process.process_manager import ProcessManager
from process.scheduler import Scheduler


def test_lock_acquire_free_and_contention_with_ports() -> None:
    blocked: List[int] = []
    woke: List[int] = []

    def block(pid: int) -> None:
        blocked.append(pid)

    def wake(pid: int) -> None:
        woke.append(pid)

    ports = SyncPorts(block_process=block, wake_process=wake, log_scheduler=None)
    lock = Lock("L")
    assert lock.acquire(1, ports) is True
    assert lock.owner_pid == 1
    assert lock.acquire(2, ports) is False
    assert list(lock.waiting_queue) == [2]
    assert blocked == [2]
    assert lock.release(2, ports) is False
    assert lock.release(1, ports) is True
    assert lock.owner_pid == 2
    assert woke == [2]
    assert list(lock.waiting_queue) == []


def test_semaphore_wait_blocks_signal_wakes() -> None:
    blocked: List[int] = []
    woke: List[int] = []

    def block(pid: int) -> None:
        blocked.append(pid)

    def wake(pid: int) -> None:
        woke.append(pid)

    ports = SyncPorts(block_process=block, wake_process=wake, log_scheduler=None)
    sem = CountingSemaphore("S", 0)
    assert sem.wait(10, ports) is False
    assert blocked == [10]
    sem.signal(99, ports)
    assert woke == [10]
    assert sem.wait(10, ports) is True


def test_shared_buffer_capacity() -> None:
    buf = SharedBuffer[int](2, name="b")
    buf.produce(1)
    buf.produce(2)
    try:
        buf.produce(3)
        assert False, "expected RuntimeError on overflow"
    except RuntimeError:
        pass
    assert buf.consume() == 1
    assert buf.consume() == 2
    try:
        buf.consume()
        assert False, "expected RuntimeError on underflow"
    except RuntimeError:
        pass


def test_scheduler_skips_blocked() -> None:
    pm = ProcessManager()
    sch = Scheduler()
    a = pm.create_process("a", cpu_burst_time=1)
    b = pm.create_process("b", cpu_burst_time=1)
    pm.change_state(a.pid, ProcessState.BLOCKED)
    pm.change_state(b.pid, ProcessState.READY)
    sch.enqueue(a)
    sch.enqueue(b)
    table = pm.pid_table()
    nxt = sch.pick_next(table)
    assert nxt is not None and nxt.pid == b.pid
