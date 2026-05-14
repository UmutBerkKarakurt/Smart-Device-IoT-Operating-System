"""In-memory hierarchical file system with metadata and per-file exclusive locks."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Deque, Dict, List, Optional, Set

from os_core.logger import get_logger

if TYPE_CHECKING:
    from filesystem.file_system_ports import FileSystemPorts

_log = get_logger("FileSystem")


def normalize_path(path: str) -> str:
    """Absolute POSIX-style path with single leading slash."""
    p = path.replace("\\", "/").strip()
    if not p or p == "/":
        return "/"
    if not p.startswith("/"):
        p = "/" + p
    parts: List[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    return "/" + "/".join(parts) if parts else "/"


def parent_dir(path: str) -> str:
    n = normalize_path(path)
    if n == "/":
        return "/"
    parent = n.rsplit("/", 1)[0]
    return parent if parent else "/"


def basename(path: str) -> str:
    n = normalize_path(path)
    if n == "/":
        return ""
    return n.rsplit("/", 1)[-1]


@dataclass
class FileMetadata:
    """Inode-level metadata (simulation ticks for timestamps)."""

    name: str
    path: str
    size: int
    created_at: int
    modified_at: int
    owner_pid: Optional[int]
    open_count: int
    locked_by: Optional[int]


@dataclass
class _Inode:
    content: str
    meta: FileMetadata
    lock_owner: Optional[int] = None
    lock_waiters: Deque[int] = field(default_factory=deque)


class FileSystem:
    """Directories + files; exclusive file lock with FIFO waiters; no real disk."""

    def __init__(self, *, initial_tick: int = 0) -> None:
        self._tick: int = initial_tick
        self._dirs: Set[str] = {"/"}
        self._files: Dict[str, _Inode] = {}

    def set_simulation_tick(self, tick: int) -> None:
        """Caller advances logical time for created_at / modified_at."""
        self._tick = tick

    def _touch_meta_size(self, inode: _Inode) -> None:
        inode.meta.size = len(inode.content)
        inode.meta.modified_at = self._tick

    def mkdir(self, path: str, *, creator_pid: Optional[int] = None) -> bool:
        p = normalize_path(path)
        if p == "/":
            _log(f"mkdir {p!r}: ok (root exists)")
            return True
        parent = parent_dir(p)
        if parent != "/" and parent not in self._dirs:
            _log(f"mkdir {p!r}: no parent directory {parent!r}")
            return False
        if p in self._dirs:
            _log(f"mkdir {p!r}: already exists")
            return False
        if p in self._files:
            _log(f"mkdir {p!r}: path is a file")
            return False
        self._dirs.add(p)
        _log(f"mkdir {p!r}: ok pid={creator_pid}")
        return True

    def list_dir(self, path: str) -> Optional[List[str]]:
        d = normalize_path(path)
        if d not in self._dirs:
            _log(f"list_dir {d!r}: not a directory")
            return None
        names: List[str] = []
        for dir_path in self._dirs:
            if dir_path in (d, "/"):
                continue
            if parent_dir(dir_path) == d:
                names.append(basename(dir_path))
        for fp in self._files:
            if parent_dir(fp) == d:
                names.append(basename(fp))
        names.sort()
        _log(f"list_dir {d!r}: {names}")
        return names

    def create(self, path: str, *, creator_pid: Optional[int] = None) -> bool:
        p = normalize_path(path)
        if p in self._files:
            _log(f"create {p!r}: already exists")
            return False
        if p in self._dirs:
            _log(f"create {p!r}: is a directory")
            return False
        par = parent_dir(p)
        if par not in self._dirs:
            _log(f"create {p!r}: parent {par!r} missing")
            return False
        name = basename(p)
        meta = FileMetadata(
            name=name,
            path=p,
            size=0,
            created_at=self._tick,
            modified_at=self._tick,
            owner_pid=creator_pid,
            open_count=0,
            locked_by=None,
        )
        self._files[p] = _Inode(content="", meta=meta)
        _log(f"create {p!r}: ok (empty) pid={creator_pid}")
        return True

    def file_exists(self, path: str) -> bool:
        return normalize_path(path) in self._files

    def get_metadata(self, path: str) -> Optional[FileMetadata]:
        p = normalize_path(path)
        inode = self._files.get(p)
        if inode is None:
            _log(f"metadata {p!r}: no such file")
            return None
        inode.meta.locked_by = inode.lock_owner
        inode.meta.size = len(inode.content)
        _log(
            f"metadata {p!r}: size={inode.meta.size} open_count={inode.meta.open_count} "
            f"locked_by={inode.meta.locked_by}"
        )
        return inode.meta

    def read(self, path: str) -> Optional[str]:
        p = normalize_path(path)
        inode = self._files.get(p)
        if inode is None:
            _log(f"read {p!r}: no such file")
            return None
        _log(f"read {p!r}: {len(inode.content)} chars")
        return inode.content

    def write(self, path: str, content: str, *, writer_pid: Optional[int] = None) -> bool:
        p = normalize_path(path)
        inode = self._files.get(p)
        if inode is None:
            _log(f"write {p!r}: no such file")
            return False
        inode.content = content
        self._touch_meta_size(inode)
        inode.meta.owner_pid = writer_pid
        _log(f"write {p!r}: {len(content)} chars pid={writer_pid}")
        return True

    def append(self, path: str, fragment: str, *, writer_pid: Optional[int] = None) -> bool:
        p = normalize_path(path)
        inode = self._files.get(p)
        if inode is None:
            _log(f"append {p!r}: no such file")
            return False
        inode.content += fragment
        self._touch_meta_size(inode)
        inode.meta.owner_pid = writer_pid
        _log(f"append {p!r}: +{len(fragment)} chars pid={writer_pid}")
        return True

    def delete(self, path: str) -> bool:
        p = normalize_path(path)
        if p not in self._files:
            _log(f"delete {p!r}: no such file")
            return False
        inode = self._files[p]
        if inode.meta.open_count > 0:
            _log(f"delete {p!r}: busy open_count={inode.meta.open_count}")
            return False
        if inode.lock_owner is not None:
            _log(f"delete {p!r}: locked by pid={inode.lock_owner}")
            return False
        del self._files[p]
        _log(f"delete {p!r}: ok")
        return True

    def try_acquire_exclusive_lock(self, pid: int, path: str, ports: Optional["FileSystemPorts"] = None) -> bool:
        """Grant exclusive lock or enqueue ``pid`` and block when ``ports`` is set."""
        p = normalize_path(path)
        inode = self._files.get(p)
        if inode is None:
            _log(f"lock acquire {p!r}: no such file")
            return False
        if inode.lock_owner is None or inode.lock_owner == pid:
            inode.lock_owner = pid
            inode.meta.locked_by = pid
            _log(f"lock acquire {p!r}: granted pid={pid}")
            return True
        _log(f"lock contention {p!r}: holder pid={inode.lock_owner} waiter pid={pid}")
        if ports is not None:
            if pid not in inode.lock_waiters:
                inode.lock_waiters.append(pid)
            if ports.log_scheduler:
                ports.log_scheduler(f"pid={pid} blocked on file lock waiting for {p!r}")
            ports.block_process(pid)
        return False

    def release_exclusive_lock(self, pid: int, path: str, ports: Optional["FileSystemPorts"] = None) -> None:
        p = normalize_path(path)
        inode = self._files.get(p)
        if inode is None:
            return
        if inode.lock_owner != pid:
            _log(f"lock release {p!r}: pid={pid} not owner (owner={inode.lock_owner})")
            return
        inode.lock_owner = None
        inode.meta.locked_by = None
        _log(f"lock release {p!r}: pid={pid}")
        if not inode.lock_waiters:
            return
        next_pid = inode.lock_waiters.popleft()
        inode.lock_owner = next_pid
        inode.meta.locked_by = next_pid
        _log(f"lock acquire {p!r}: granted pid={next_pid} (from wait queue)")
        if ports is not None:
            if ports.log_scheduler:
                ports.log_scheduler(f"wake pid={next_pid} after file lock on {p!r}")
            ports.wake_process(next_pid)

    def note_open(self, path: str) -> None:
        p = normalize_path(path)
        inode = self._files.get(p)
        if inode is None:
            return
        inode.meta.open_count += 1

    def note_close(self, path: str) -> None:
        p = normalize_path(path)
        inode = self._files.get(p)
        if inode is None:
            return
        inode.meta.open_count = max(0, inode.meta.open_count - 1)
