"""Input validation and safety guards for the aiortc SFU adapter."""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from .constants import (
    MAX_DTLS_FINGERPRINTS,
    MAX_ICE_CANDIDATE_BYTES,
    MAX_ICE_UFRAG_LEN,
    MAX_ID_LEN,
    MAX_OUTPUT_DIR_LEN,
    MAX_PAYLOAD_DEPTH,
    MAX_PAYLOAD_KEYS,
    MAX_PAYLOAD_STRING,
    MAX_ROOM_ID_LEN,
    MAX_SDP_BYTES,
    MAX_TRACK_KIND_LEN,
    _ID_RE_SRC,
)
from .errors import SFUValidationError

_ID_RE = re.compile(_ID_RE_SRC)


def _require_str(value: Any, name: str, *, max_len: int) -> str:
    if not isinstance(value, str):
        raise SFUValidationError(f"{name} must be a string")
    if not value:
        raise SFUValidationError(f"{name} must not be empty")
    if "\x00" in value:
        raise SFUValidationError(f"{name} contains NUL bytes")
    if len(value) > max_len:
        raise SFUValidationError(f"{name} exceeds maximum length ({max_len})")
    return value


def validate_identifier(
    value: Any, name: str = "id", *, max_len: int = MAX_ID_LEN
) -> str:
    """Validate a peer/room/transport/producer/consumer id (fail-closed)."""
    text = _require_str(value, name, max_len=max_len)
    if not _ID_RE.match(text):
        raise SFUValidationError(f"{name} contains invalid characters")
    return text


def validate_room_id(value: Any, name: str = "room_id") -> str:
    return validate_identifier(value, name, max_len=MAX_ROOM_ID_LEN)


def validate_track_kind(kind: Any) -> str:
    text = _require_str(kind, "media kind", max_len=MAX_TRACK_KIND_LEN).lower()
    if text not in {"audio", "video"}:
        raise SFUValidationError("media kind must be 'audio' or 'video'")
    return text


def validate_sdp_payload(
    sdp: Any, name: str = "SDP", *, max_bytes: int = MAX_SDP_BYTES
) -> str:
    """Validate an SDP blob; returns the sanitized SDP string."""
    text = _require_str(sdp, name, max_len=max_bytes)
    if "\r" not in text and "\n" not in text:
        # An SDP is line-oriented; a single blob is malformed.
        raise SFUValidationError(f"{name} is malformed")
    lowered = text.lower()
    if "v=0" not in lowered or "m=" not in lowered:
        raise SFUValidationError(f"{name} is not a valid session description")
    return text


def validate_ice_candidate_payload(
    candidate: Any,
    sdp_mid: Any = None,
    sdp_mline_index: Any = None,
) -> tuple[str, str | None, int | None]:
    """Validate a trickled ICE candidate and its m-line binding."""
    cand = _require_str(
        candidate, "ICE candidate", max_len=MAX_ICE_CANDIDATE_BYTES
    ).strip()
    if not cand.lower().startswith("candidate:"):
        raise SFUValidationError("ICE candidate is malformed")
    if any(ch in cand for ch in ("\r", "\n", "\x00")):
        raise SFUValidationError("ICE candidate contains control characters")

    mid: str | None = None
    if sdp_mid is not None:
        mid = _require_str(sdp_mid, "sdpMid", max_len=MAX_ICE_UFRAG_LEN)
    idx: int | None = None
    if sdp_mline_index is not None:
        if isinstance(sdp_mline_index, bool) or not isinstance(sdp_mline_index, int):
            raise SFUValidationError("sdpMLineIndex must be an integer")
        if sdp_mline_index < 0 or sdp_mline_index > 65535:
            raise SFUValidationError("sdpMLineIndex is out of range")
        idx = sdp_mline_index
    if mid is None and idx is None:
        raise SFUValidationError("ICE candidate requires sdpMid or sdpMLineIndex")
    return cand, mid, idx


def _bounded_strings(value: Any, depth: int) -> None:
    if depth > MAX_PAYLOAD_DEPTH:
        raise SFUValidationError("payload nesting is too deep")
    if isinstance(value, str):
        if len(value) > MAX_PAYLOAD_STRING or "\x00" in value:
            raise SFUValidationError("payload string is too large or invalid")
        return
    if isinstance(value, Mapping):
        if len(value) > MAX_PAYLOAD_KEYS:
            raise SFUValidationError("payload has too many keys")
        for key, inner in value.items():
            if not isinstance(key, str) or len(key) > 128 or "\x00" in key:
                raise SFUValidationError("payload key is invalid")
            _bounded_strings(inner, depth + 1)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_PAYLOAD_KEYS:
            raise SFUValidationError("payload sequence is too large")
        for inner in value:
            _bounded_strings(inner, depth + 1)
        return
    if value is None or isinstance(value, (bool, int, float, bytes, bytearray)):
        if isinstance(value, (bytes, bytearray)) and len(value) > MAX_PAYLOAD_STRING:
            raise SFUValidationError("payload bytes value is too large")
        return
    # Unknown types are not accepted in control payloads.
    raise SFUValidationError(
        f"payload contains unsupported type: {type(value).__name__}"
    )


def validate_payload_mapping(value: Any, name: str = "payload") -> dict[str, Any]:
    """Validate a control/RTP payload mapping (bounded, string-keyed)."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SFUValidationError(f"{name} must be a mapping")
    _bounded_strings(value, 1)
    return dict(value)


def validate_dtls_parameters(params: Any) -> dict[str, Any]:
    """Validate client-supplied DTLS parameters."""
    mapping = validate_payload_mapping(params, "dtls_parameters")
    fingerprints = mapping.get("fingerprints")
    if fingerprints is not None:
        if (
            not isinstance(fingerprints, list)
            or len(fingerprints) > MAX_DTLS_FINGERPRINTS
        ):
            raise SFUValidationError("dtls_parameters.fingerprints is invalid")
        for fp in fingerprints:
            if not isinstance(fp, Mapping):
                raise SFUValidationError("dtls fingerprint entry is invalid")
            alg = fp.get("algorithm")
            val = fp.get("value")
            if not isinstance(alg, str) or not alg or len(alg) > 64:
                raise SFUValidationError("dtls fingerprint algorithm is invalid")
            if not isinstance(val, str) or not val or len(val) > 256:
                raise SFUValidationError("dtls fingerprint value is invalid")
    role = mapping.get("role")
    if role is not None and (not isinstance(role, str) or len(role) > 32):
        raise SFUValidationError("dtls role is invalid")
    return mapping


def validate_recording_output_dir(path: Any) -> str:
    """Validate/sanitize a recording output directory (no traversal)."""
    text = _require_str(path, "output_dir", max_len=MAX_OUTPUT_DIR_LEN)
    # Normalize and require an absolute, non-root-only-ish safe location.
    norm = os.path.normpath(os.path.abspath(text))
    if ".." in os.path.normpath(text).split(os.sep):
        raise SFUValidationError("output_dir must not traverse paths")
    return norm


__all__ = [
    "validate_identifier",
    "validate_room_id",
    "validate_track_kind",
    "validate_sdp_payload",
    "validate_ice_candidate_payload",
    "validate_payload_mapping",
    "validate_dtls_parameters",
    "validate_recording_output_dir",
]
