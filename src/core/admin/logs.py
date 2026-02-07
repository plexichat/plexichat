"""
Log management for PlexiChat Admin.
"""

import os
import zipfile
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
import re

import utils.logger as logger
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
            logs.append({
                "filename": item.name,
                "size": stats.st_size,
                "modified": int(stats.st_mtime * 1000),
                "is_zipped": item.name.endswith(".zip")
            })
            
    # Sort by modified time desc (newest first)
    logs.sort(key=lambda x: x["modified"], reverse=True)
    return logs

def read_log_lines(
    filename: str, 
    limit: int = 1000, 
    offset: int = 0, 
    search: Optional[str] = None, 
    level_filter: Optional[str] = None
) -> Dict[str, Any]:
    """Read lines from a log file with filtering and pagination."""
    log_dir = get_log_dir()
    log_path = log_dir / filename
    
    if not log_path.exists():
        raise FileNotFoundError(f"Log file {filename} not found")
        
    lines = []
    
    # Handle zipped logs
    if filename.endswith(".zip"):
        with zipfile.ZipFile(log_path, 'r') as zf:
            # Assume first file in zip is the log
            log_filename = zf.namelist()[0]
            with zf.open(log_filename, 'r') as f:
                content = f.read().decode('utf-8', errors='replace')
                raw_lines = content.splitlines()
    else:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            raw_lines = f.readlines()
            
    # Process lines
    # Format: 2026-02-07 12:34:56,789 - LEVEL - Message
    # Regex to parse line
    log_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?)\s*-\s*(\w+)\s*-\s*(.*)$')
    
    processed_lines = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
            
        match = log_pattern.match(line)
        if match:
            timestamp, level, message = match.groups()
        else:
            # Handle multi-line log entries (like stack traces) by attaching to previous line or marking as info
            timestamp = ""
            level = "INFO"
            message = line
            
        # Apply level filter
        if level_filter and level.upper() != level_filter.upper():
            continue
            
        # Apply search filter
        if search and search.lower() not in line.lower():
            continue
            
        processed_lines.append({
            "timestamp": timestamp,
            "level": level,
            "message": message,
            "raw": line
        })
        
    # Apply pagination (from newest to oldest usually, or just slice)
    # The user asked for "entirety of the logs", but for large files we should paginate
    total_count = len(processed_lines)
    
    # We'll return them in chronological order as they appear in the file
    start = max(0, total_count - limit - offset) if offset == 0 else offset
    end = min(total_count, start + limit)
    
    return {
        "filename": filename,
        "total_lines": total_count,
        "lines": processed_lines[start:end],
        "limit": limit,
        "offset": offset
    }
