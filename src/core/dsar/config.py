"""DSAR configuration resolution.

The canonical DSAR defaults live under ``authentication.dsar`` in the
generated config (see ``config_defaults_authentication``), while some
deployments and legacy call sites read ``dsar`` at the top level. Resolve
both locations so operator overrides are honored regardless of nesting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import utils.config as config


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_dsar_config(override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the effective DSAR config.

    Precedence (lowest to highest): nested ``authentication.dsar`` defaults,
    top-level ``dsar`` overrides, explicit ``override`` (used by workers/tests
    that inject config).
    """
    nested = config.get("authentication", {})
    if isinstance(nested, dict):
        nested = nested.get("dsar", {}) or {}
    else:
        nested = {}
    top = config.get("dsar", {}) or {}
    if not isinstance(nested, dict):
        nested = {}
    if not isinstance(top, dict):
        top = {}
    merged = _deep_merge(nested, top)
    if override:
        merged = _deep_merge(merged, override)
    return merged


def dsar_enabled() -> bool:
    return bool(get_dsar_config().get("enabled", True))


def allowed_export_formats() -> List[str]:
    formats = get_dsar_config().get("export_formats", ["json", "zip"])
    if not isinstance(formats, list) or not formats:
        formats = ["json", "zip"]
    out = []
    for fmt in formats:
        if isinstance(fmt, str) and fmt.strip():
            out.append(fmt.strip().lower())
    return out or ["json", "zip"]


def default_export_format() -> str:
    allowed = allowed_export_formats()
    default = get_dsar_config().get("default_format", allowed[0])
    if not isinstance(default, str):
        default = allowed[0]
    default = default.strip().lower()
    return default if default in allowed else allowed[0]


def auto_approve_after_hours() -> Optional[int]:
    """Resolve the DSAR auto-approval policy.

    ``None`` means no explicit policy was set; callers should fall back to the
    legacy ``require_admin_review`` behavior. ``0`` approves immediately,
    ``-1`` never auto-approves, and any positive value is the number of hours
    a pending request may wait before it is auto-approved.
    """
    value = get_dsar_config().get("auto_approve_after_hours")
    if value is None:
        return None
    try:
        hours = int(value)
    except (TypeError, ValueError):
        return None
    if hours < -1:
        return -1
    return hours


def harvester_config() -> Dict[str, Any]:
    cfg = get_dsar_config().get("harvester", {}) or {}
    return cfg if isinstance(cfg, dict) else {}


def retention_days() -> int:
    try:
        return max(1, int(get_dsar_config().get("retention_days", 7)))
    except (TypeError, ValueError):
        return 7


def audit_log_config() -> Dict[str, Any]:
    cfg = get_dsar_config().get("audit_log", {}) or {}
    return cfg if isinstance(cfg, dict) else {}


def local_export_path() -> str:
    default_local = str(Path.home() / ".plexichat" / "data" / "exports" / "dsar")
    path = get_dsar_config().get("local_path", default_local)
    return str(path) if path else default_local


def s3_path_prefix() -> str:
    prefix = get_dsar_config().get("s3_path_prefix", "dsar-exports")
    return str(prefix) if prefix else "dsar-exports"


def request_rate_limit() -> Dict[str, int]:
    cfg = get_dsar_config().get("rate_limit", {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    try:
        requests = max(1, int(cfg.get("requests", 1)))
    except (TypeError, ValueError):
        requests = 1
    try:
        window_seconds = max(1, int(cfg.get("window_seconds", 86400)))
    except (TypeError, ValueError):
        window_seconds = 86400
    return {"requests": requests, "window_seconds": window_seconds}
