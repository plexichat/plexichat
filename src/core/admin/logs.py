"""
Log management for Plexichat Admin.
"""

import os
import zipfile
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from collections import OrderedDict, deque
from itertools import islice
import io
import re

import utils.config as config
import zstandard

from src.core.config.snapshot import file_version
from src.core.telemetry.scrubbing import redact_secrets, scrub_control_characters

_COMPRESSED_LOG_SUFFIXES = (".log.zst", ".log.zip")

# Parsing the same log line format on every request is pure overhead: the
# pattern is immutable, so compile it once at import time.
_LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?)\s*-\s*(\w+)\s*-\s*(.*)$"
)

# Read the stream in bounded batches instead of issuing one Python-level
# callback per line. Batches keep memory bounded while amortising loop setup.
_LINE_BATCH_SIZE = 2000

# Composite index over the common admin-log filter. Keys are
# (resolved path, file version, level, search); values are the filtered result
# (total count + the bounded tail of matching lines). Repeated page/filter
# requests for an unchanged file are served from this index instead of
# decompressing and re-parsing the whole log. The index is LRU-bounded so a
# long-lived admin session cannot grow it without limit.
_MAX_INDEXED_FILTERS = 32
_LOG_FILTER_INDEX: "OrderedDict[Tuple[Any, ...], Tuple[int, List[Dict[str, Any]]]]" = (
    OrderedDict()
)


def _is_compressed_log(name: str) -> bool:
    return name.endswith(_COMPRESSED_LOG_SUFFIXES)


def get_log_dir() -> Path:
    """Get the log directory from config or default."""
    # Use standard location from main.py
    media_config = config.get("media", {})
    log_dir = media_config.get("logs_dir")

    if not log_dir:
        # Fallback to default
        log_dir = Path.home() / ".plexichat" / "logs"
    else:
        log_dir = Path(os.path.expanduser(log_dir))

    if not log_dir.exists():
        log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def list_logs() -> List[Dict[str, Any]]:
    """List available log files with metadata."""
    log_dir = get_log_dir()
    logs = []

    for item in log_dir.iterdir():
        if item.is_file() and (item.suffix == ".log" or _is_compressed_log(item.name)):
            stats = item.stat()
            logs.append(
                {
                    "filename": item.name,
                    "size": stats.st_size,
                    "modified": int(stats.st_mtime * 1000),
                    "is_zipped": _is_compressed_log(item.name),
                }
            )

    # Sort by modified time desc (newest first)
    logs.sort(key=lambda x: x["modified"], reverse=True)
    return logs


def _iter_batches(stream, size: int = _LINE_BATCH_SIZE):
    """Yield bounded lists of lines from a text stream."""
    iterator = iter(stream)
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            return
        yield batch


def _open_log_text(log_path: Path, filename: str):
    """Return an open text stream for a plain, zstd or zip log file."""
    if filename.endswith(".zip"):
        zf = zipfile.ZipFile(log_path, "r")
        names = [name for name in zf.namelist() if not name.endswith("/")]
        if not names:
            zf.close()
            raise FileNotFoundError(f"Log file {filename} not found")
        log_filename = names[0]
        # A member name is archive-controlled data: refuse a path-like one
        # rather than trusting the archive to be well formed.
        if log_filename != os.path.basename(log_filename):
            zf.close()
            raise ValueError("Log archive member has an unsafe name")
        try:
            member = zf.open(log_filename, "r")
        except Exception:
            zf.close()
            raise
        text = io.TextIOWrapper(member, encoding="utf-8", errors="replace")
        return _ArchiveStream(text, zf)
    if filename.endswith(".zst"):
        raw = open(log_path, "rb")
        try:
            reader = zstandard.ZstdDecompressor().stream_reader(raw)
        except Exception:
            raw.close()
            raise
        text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
        return _ArchiveStream(text, raw)
    return _ArchiveStream(open(log_path, "r", encoding="utf-8", errors="replace"), None)


class _ArchiveStream:
    """Context manager that closes both the text wrapper and its owner."""

    def __init__(self, text, owner):
        self._text = text
        self._owner = owner

    def __iter__(self):
        return iter(self._text)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._text.close()
        if self._owner is not None:
            self._owner.close()
        return False


def _read_filtered(
    log_path: Path, filename: str, level_filter: Optional[str], search: Optional[str]
) -> Tuple[int, List[Dict[str, Any]]]:
    """Read and filter a log file in batches, returning count + bounded tail."""
    level_upper = level_filter.upper() if level_filter else None
    search_lower = search.lower() if search else None

    processed_lines: deque = deque(maxlen=10000)
    total_count = 0
    pattern = _LOG_PATTERN

    with _open_log_text(log_path, filename) as text_stream:
        for batch in _iter_batches(text_stream):
            for raw_line in batch:
                line = raw_line.strip()
                if not line:
                    continue

                match = pattern.match(line)
                if match:
                    timestamp, level, message = match.groups()
                else:
                    timestamp = ""
                    level = "INFO"
                    message = line

                if level_upper and level.upper() != level_upper:
                    continue
                if search_lower and search_lower not in line.lower():
                    continue

                total_count += 1
                # Log lines are untrusted free text: redact secrets first, then
                # neutralise embedded newlines, carriage returns and ANSI escape
                # sequences so a low-privileged user whose input reached a log
                # cannot repaint, clear or forge entries in the admin viewer.
                # Search still runs on the raw line; neither served field leaks
                # a secret or a control sequence.
                processed_lines.append(
                    {
                        "timestamp": timestamp,
                        "level": level,
                        "message": scrub_control_characters(redact_secrets(message)),
                        "raw": scrub_control_characters(redact_secrets(line)),
                    }
                )

    return total_count, list(processed_lines)


def _indexed_filtered(
    log_path: Path,
    filename: str,
    version: Tuple[Any, ...],
    level_filter: Optional[str],
    search: Optional[str],
) -> Tuple[int, List[Dict[str, Any]]]:
    """Serve a filtered read from the composite index, populating on a miss."""
    key = (
        str(log_path),
        version,
        level_filter.upper() if level_filter else None,
        search.lower() if search else None,
    )
    cached = _LOG_FILTER_INDEX.get(key)
    if cached is not None:
        _LOG_FILTER_INDEX.move_to_end(key)
        return cached

    result = _read_filtered(log_path, filename, level_filter, search)
    _LOG_FILTER_INDEX[key] = result
    if len(_LOG_FILTER_INDEX) > _MAX_INDEXED_FILTERS:
        _LOG_FILTER_INDEX.popitem(last=False)
    return result


def clear_log_filter_index() -> None:
    """Drop cached filtered log reads (e.g. after a log rotation or in tests)."""
    _LOG_FILTER_INDEX.clear()


def index_stats() -> Dict[str, int]:
    """Return the number of cached filter results and its bound."""
    return {"entries": len(_LOG_FILTER_INDEX), "max_entries": _MAX_INDEXED_FILTERS}


def read_log_lines(
    filename: str,
    limit: int = 1000,
    offset: int = 0,
    search: Optional[str] = None,
    level_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Read lines from a log file with filtering and pagination."""
    log_dir = get_log_dir().resolve()
    if Path(filename).name != filename or ".." in Path(filename).parts:
        raise FileNotFoundError(f"Log file {filename} not found")
    if not (filename.endswith(".log") or _is_compressed_log(filename)):
        raise FileNotFoundError(f"Log file {filename} not found")
    log_path = (log_dir / filename).resolve()
    try:
        log_path.relative_to(log_dir)
    except ValueError:
        raise FileNotFoundError(f"Log file {filename} not found")

    if not log_path.exists() or not log_path.is_file():
        raise FileNotFoundError(f"Log file {filename} not found")

    if limit < 1:
        limit = 1
    if limit > 2000:
        limit = 2000
    if offset < 0:
        offset = 0

    version = file_version(str(log_path))
    total_count, processed_lines = _indexed_filtered(
        log_path, filename, version, level_filter, search
    )

    base_index = max(0, total_count - len(processed_lines))
    if offset == 0:
        start = max(0, len(processed_lines) - limit)
    else:
        if offset < base_index:
            offset = base_index
        start = max(0, offset - base_index)
    end = min(len(processed_lines), start + limit)

    return {
        "filename": filename,
        "total_lines": total_count,
        # Hand back fresh dicts so mutating a response cannot corrupt the
        # cached filter result served to the next request.
        "lines": [dict(line) for line in processed_lines[start:end]],
        "limit": limit,
        "offset": offset,
    }


def read_log_lines_batch(
    filenames: List[str],
    limit: int = 1000,
    offset: int = 0,
    search: Optional[str] = None,
    level_filter: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Read several log files in one call, sharing the composite filter index.

    Resolving the log directory once and reusing the index across files avoids
    repeating config lookups and stat work for each file in an admin view.
    """
    return {
        filename: read_log_lines(filename, limit, offset, search, level_filter)
        for filename in filenames
    }
