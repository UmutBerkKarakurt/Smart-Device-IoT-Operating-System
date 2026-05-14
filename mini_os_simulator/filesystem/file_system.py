"""Simple in-memory file store (no directories, no persistence)."""

from __future__ import annotations

from typing import Dict, Optional

from os_core.logger import get_logger

_log = get_logger("FileSystem")


class FileSystem:
    def __init__(self) -> None:
        self._files: Dict[str, str] = {}

    def create(self, name: str) -> bool:
        if name in self._files:
            _log(f"create {name!r}: already exists")
            return False
        self._files[name] = ""
        _log(f"create {name!r}: ok (empty)")
        return True

    def write(self, name: str, content: str) -> bool:
        if name not in self._files:
            _log(f"write {name!r}: no such file")
            return False
        self._files[name] = content
        _log(f"write {name!r}: {len(content)} chars")
        return True

    def read(self, name: str) -> Optional[str]:
        if name not in self._files:
            _log(f"read {name!r}: no such file")
            return None
        data = self._files[name]
        _log(f"read {name!r}: {len(data)} chars")
        return data

    def delete(self, name: str) -> bool:
        if name not in self._files:
            _log(f"delete {name!r}: no such file")
            return False
        del self._files[name]
        _log(f"delete {name!r}: ok")
        return True
