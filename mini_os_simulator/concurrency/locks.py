"""Simple non-reentrant mutex-style lock (skeleton for later wait queues)."""

from __future__ import annotations

from typing import Optional

from os_core.logger import get_logger

_log = get_logger("Concurrency")


class Lock:
    """One owner at a time; acquire fails if another pid holds the lock."""

    def __init__(self, name: str = "mutex") -> None:
        self.name = name
        self._owner: Optional[int] = None

    @property
    def owner(self) -> Optional[int]:
        return self._owner

    def acquire(self, pid: int) -> bool:
        if self._owner is None:
            self._owner = pid
            _log(f"{self.name}: acquired by pid={pid}")
            return True
        if self._owner == pid:
            _log(f"{self.name}: pid={pid} already holds lock")
            return True
        _log(f"{self.name}: acquire failed for pid={pid} (held by {self._owner})")
        return False

    def release(self, pid: int) -> bool:
        if self._owner is None:
            _log(f"{self.name}: release ignored (not held)")
            return False
        if self._owner != pid:
            _log(f"{self.name}: release denied pid={pid} owner={self._owner}")
            return False
        self._owner = None
        _log(f"{self.name}: released by pid={pid}")
        return True
