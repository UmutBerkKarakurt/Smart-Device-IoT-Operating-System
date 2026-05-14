"""Fixed-capacity shared buffer for deterministic producer/consumer demos."""

from __future__ import annotations

from collections import deque
from typing import Deque, Generic, Optional, TypeVar

from os_core.logger import get_logger

_log = get_logger("Concurrency")

T = TypeVar("T")


class SharedBuffer(Generic[T]):
    def __init__(self, capacity: int, *, name: str = "buffer") -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.name = name
        self._capacity = int(capacity)
        self._items: Deque[T] = deque()

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        return len(self._items)

    def produce(self, item: T) -> None:
        if len(self._items) >= self._capacity:
            raise RuntimeError(
                f"{self.name}: produce rejected (full {len(self._items)}/{self._capacity})"
            )
        self._items.append(item)
        _log(f"{self.name}: produce item={item!r} size={len(self._items)}/{self._capacity}")

    def consume(self) -> T:
        if not self._items:
            raise RuntimeError(f"{self.name}: consume rejected (empty)")
        item = self._items.popleft()
        _log(f"{self.name}: consume item={item!r} size={len(self._items)}/{self._capacity}")
        return item

    def peek(self) -> Optional[T]:
        return self._items[0] if self._items else None
