"""Shared configuration helpers.

Exposes :mod:`src.core.config.snapshot`, a cached, versioned view over
configuration data that invalidates on source-file mtime instead of a wall-clock
TTL, with an optional short staleness ceiling that throttles version checks on
hot paths.
"""

from .snapshot import VersionedConfigSnapshot, config_source_version, file_version

__all__ = ["VersionedConfigSnapshot", "config_source_version", "file_version"]
