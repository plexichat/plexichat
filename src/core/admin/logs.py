"""
Log management for PlexiChat Admin.
"""

import os
import zipfile
from pathlib import Path
from typing import List, Optional, Dict, Any
from collections import deque
import io
import re

import utils.config as config


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
        if item.is_file() and (item.suffix == ".log" or item.name.endswith(".log.zip")):
            stats = item.stat()
            logs.append(
                {
                    "filename": item.name,
                    "size": stats.st_size,
                    "modified": int(stats.st_mtime * 1000),
                    "is_zipped": item.name.endswith(".zip"),
                }
            )

    # Sort by modified time desc (newest first)
    logs.sort(key=lambda x: x["modified"], reverse=True)
    return logs


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
    if not (filename.endswith(".log") or filename.endswith(".log.zip")):
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

    log_pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?)\s*-\s*(\w+)\s*-\s*(.*)$"
    )

    processed_lines = deque(maxlen=10000)
    total_count = 0

    def handle_line(line: str) -> None:
        nonlocal total_count
        line = line.strip()
        if not line:
            return

        match = log_pattern.match(line)
        if match:
            timestamp, level, message = match.groups()
        else:
            timestamp = ""
            level = "INFO"
            message = line

        if level_filter and level.upper() != level_filter.upper():
            return

        if search and search.lower() not in line.lower():
            return

        total_count += 1
        processed_lines.append(
            {"timestamp": timestamp, "level": level, "message": message, "raw": line}
        )

    if filename.endswith(".zip"):
        with zipfile.ZipFile(log_path, "r") as zf:
            names = [name for name in zf.namelist() if not name.endswith("/")]
            if not names:
                raise FileNotFoundError(f"Log file {filename} not found")
            log_filename = names[0]
            with zf.open(log_filename, "r") as f:
                text_stream = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
                for line in text_stream:
                    handle_line(line)
    else:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                handle_line(line)

    processed_list = list(processed_lines)
    base_index = max(0, total_count - len(processed_list))
    if offset == 0:
        start = max(0, len(processed_list) - limit)
    else:
        if offset < base_index:
            offset = base_index
        start = max(0, offset - base_index)
    end = min(len(processed_list), start + limit)

    return {
        "filename": filename,
        "total_lines": total_count,
        "lines": processed_list[start:end],
        "limit": limit,
        "offset": offset,
    }
