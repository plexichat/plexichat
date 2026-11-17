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


def privacy_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Effective ``dsar.privacy`` configuration block.

    ``cfg`` lets a generation caller reuse the DSAR config snapshot it already
    resolved, instead of re-reading and re-merging the whole config tree.
    When omitted the behaviour is unchanged.
    """
    resolved = get_dsar_config() if cfg is None else cfg
    block = resolved.get("privacy", {}) or {}
    return block if isinstance(block, dict) else {}


def legally_required_pii_fields(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Third-party PII fields an operator must disclose by law.

    By default nothing is exempt: every third-party identifier is redacted.
    Deployments subject to a disclosure obligation can opt specific fields
    back in by name via ``dsar.privacy.legally_required_pii_fields``.

    ``cfg`` is the optional resolved DSAR config snapshot (see
    :func:`get_dsar_config`); passing it avoids a second resolution of the
    config tree on the export-generation hot path.
    """
    fields = privacy_config(cfg).get("legally_required_pii_fields", [])
    if not isinstance(fields, list):
        return []
    return [f.strip() for f in fields if isinstance(f, str) and f.strip()]


def export_quotas(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """Resolve hard bounds for a single DSAR export.

    ``dsar.export_quotas`` may lower any of these bounds; malformed or
    non-positive values fall back to safe defaults. Zero or negative limits
    are never honoured because they would make every export fail (or, if
    treated as unlimited, allow a single runaway export).
    """

    effective = get_dsar_config() if cfg is None else cfg
    raw = effective.get("export_quotas", {}) or {}
    if not isinstance(raw, dict):
        raw = {}

    defaults = {
        "max_records_per_category": 100_000,
        "max_total_records": 500_000,
        "max_export_bytes": 100 * 1024 * 1024,
    }
    quotas: Dict[str, int] = {}
    for key, default in defaults.items():
        try:
            quotas[key] = max(1, int(raw.get(key, default)))
        except (TypeError, ValueError):
            quotas[key] = default
    return quotas
