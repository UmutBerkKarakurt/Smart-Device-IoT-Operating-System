"""In-memory simulated file system."""

from filesystem.file_system import FileMetadata, FileSystem, normalize_path
from filesystem.file_system_ports import FileSystemPorts

__all__ = ["FileMetadata", "FileSystem", "FileSystemPorts", "normalize_path"]
