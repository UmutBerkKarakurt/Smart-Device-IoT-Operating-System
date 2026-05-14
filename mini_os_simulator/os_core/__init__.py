"""Core OS simulation utilities: logging (avoid importing Simulation here — circular imports)."""

from os_core.logger import get_logger, log

__all__ = ["get_logger", "log"]
