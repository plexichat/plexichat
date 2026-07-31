"""Shared validation helpers for SFU identifiers and recording paths."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from pathlib import Path
from urllib.parse import quote

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


def validate_identifier(value: str, field: str = "identifier") -> str:
    """Validate an SFU identifier before using it in a URL or filename."""
    if not isinstance(value, str) or not value or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Invalid {field}")
    return value


def path_segment(value: str, field: str = "identifier") -> str:
    """Encode an arbitrary protocol identifier as one safe URL path segment."""
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"Invalid {field}")
    return quote(value, safe="")


def safe_filename_component(value: str, field: str = "identifier") -> str:
    """Return a filesystem-safe component without changing its identity silently."""
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"Invalid {field}")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return digest


def stable_numeric_id(value: str, namespace: str) -> int:
    """Derive a stable, non-zero Janus-compatible numeric identifier.

    Janus requires numeric room/publisher IDs. Python's process-randomized
    ``hash()`` is unsuitable because it changes after restart and has easy
    collisions. HMAC-SHA256 gives stable, deployment-scoped values; the
    remaining collision probability is negligible for the 31-bit Janus space.
    """
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("Invalid identifier")
    secret = os.environ.get("PLEXICHAT_SFU_ID_SECRET", "plexichat-dev-sfu-id").encode()
    digest = hmac.new(secret, f"{namespace}:{value}".encode(), hashlib.sha256).digest()
    return (int.from_bytes(digest[:8], "big") % 2_147_483_646) + 1


def resolve_recording_dir(output_dir: str) -> Path:
    """Return an approved absolute recording directory.

    Direct SFU callers may not select arbitrary filesystem locations. The
    configured artifacts voice recording directory is the trust boundary;
    callers can pass that directory or a child of it.
    """
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ValueError("Recording output directory is required")
    path = Path(output_dir).expanduser().resolve()

    configured = None
    try:
        import utils.config as config

        configured = (
            config.get("artifacts", {})
            .get("voice", {})
            .get("recording", {})
            .get("output_dir")
        )
    except Exception:
        configured = None

    if configured:
        root = Path(str(configured)).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                "Recording output directory is outside the configured root"
            ) from exc
    if path.exists() and not path.is_dir():
        raise ValueError("Recording output path is not a directory")
    return path


def ensure_within_recording_dir(path: str | Path, root: str | Path) -> Path:
    """Resolve a recording path and require it to stay under ``root``."""
    root_path = Path(root).expanduser().resolve()
    candidate = Path(path).expanduser().resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(
            "Recording path escapes the configured recording directory"
        ) from exc
    return candidate
