"""Compatibility facade over SQLite Schema v6 configuration tables."""

from .schema import SCHEMA_VERSION
from .storage import read_current_configuration as read_current
from .storage import write_current_configuration as write_current

__all__ = ["SCHEMA_VERSION", "read_current", "write_current"]
