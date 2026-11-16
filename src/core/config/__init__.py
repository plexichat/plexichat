"""Shared configuration helpers.

Currently exposes :mod:`src.core.config.snapshot`, a cached, versioned view
over configuration data that invalidates on source-file mtime instead of a
wall-clock TTL.
"""

from .snapshot import VersionedConfigSnapshot, file_version

__all__ = ["VersionedConfigSnapshot", "file_version"]
