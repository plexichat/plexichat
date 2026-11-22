"""Boundary guards for voice server/link URLs and session metadata.

The signaling layer reads STUN/TURN URLs and endpoints from operator config and
returns them to every client that joins voice. A single injected URL can point
a browser at an attacker-controlled relay (or carry a ``javascript:``/data
payload into a client attribute), so every URL is scheme-checked and length-
bounded at the response boundary, never trusted from config verbatim.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    require_ws_url,
)

#: A client only needs a handful of ICE endpoints; cap the fan-out.
MAX_ICE_SERVERS = 16
MAX_ICE_URLS_PER_SERVER = 8
MAX_ICE_URL_LENGTH = 512
MAX_ENDPOINT_URL_LENGTH = 512
MAX_ICE_CREDENTIAL_LENGTH = 256

ICE_SCHEMES = frozenset({"stun", "stuns", "turn", "turns"})
DEFAULT_PUBLIC_STUN_URLS = [
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
]

# Characters that can break out of a quoted client attribute or smuggle markup
# into an echo of the URL.
_UNSAFE_URL_CHARS = frozenset("<>\"'`\\")


def validate_ice_url(value: object, field: str = "ice_url") -> str:
    """Return ``value`` if it is a bounded stun/stuns/turn/turns URL.

    Raises ``ValueError`` otherwise. ICE URLs use the ``scheme:host:port`` form
    (``urlparse`` has no hostname for them), so the host is parsed directly.
    """
    text = str(value or "").strip()
    if not text or len(text) > MAX_ICE_URL_LENGTH:
        raise ValueError(f"{field} is empty or too long")
    if any(ch in text for ch in _UNSAFE_URL_CHARS):
        raise ValueError(f"{field} contains unsafe characters")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise ValueError(f"{field} contains control characters")

    scheme, sep, rest = text.partition(":")
    if not sep or scheme.lower() not in ICE_SCHEMES:
        raise ValueError(f"{field} must use a stun/turn scheme")
    # credentials in the URL ("user:pass@host") are not legitimate here
    if "@" in rest:
        raise ValueError(f"{field} must not embed credentials")
    host = (
        rest.split("]", 1)[0] + "]" if rest.startswith("[") else rest.split(":", 1)[0]
    )
    if not host or host.startswith("["):
        if not (rest.startswith("[") and "]" in rest):
            raise ValueError(f"{field} has no host")
    return text


def filter_ice_urls(urls: Iterable[object]) -> List[str]:
    """Keep only valid ICE URLs; fall back to public STUN if none remain."""
    valid: List[str] = []
    for url in urls or ():
        try:
            valid.append(validate_ice_url(url))
        except ValueError:
            continue
        # Only the first MAX_ICE_URLS_PER_SERVER valid URLs can be returned.
        # Validating and retaining the rest of an oversized iterable would
        # grow work and memory with the input for a fixed-size result.
        if len(valid) >= MAX_ICE_URLS_PER_SERVER:
            break
    return valid[:MAX_ICE_URLS_PER_SERVER] or list(DEFAULT_PUBLIC_STUN_URLS)


def validate_server_endpoint(value: object, field: str = "endpoint") -> str:
    """Return ``value`` only if it is a bounded absolute ws(s) URL.

    The media-plane endpoint is a WebSocket URL (the SFU's ``ws_url``), so
    ws/wss are the legitimate schemes here. The absolute-scheme and
    no-credentials checks are the same as for HTTP URLs.
    """
    text = str(value or "").strip()
    if len(text) > MAX_ENDPOINT_URL_LENGTH:
        raise ValueError(f"{field} is too long")
    try:
        return require_ws_url(text, field)
    except HardeningViolation as exc:
        raise ValueError(str(exc)) from exc


def _bounded_credential(value: object, field: str) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if len(text) > MAX_ICE_CREDENTIAL_LENGTH:
        raise ValueError(f"{field} is too long")
    return text


def sanitize_ice_servers(servers: Iterable[Any]) -> List[dict]:
    """Validate and cap the ICE server list built from operator config.

    Returns plain dicts ready for the API schema. Invalid URLs are dropped and
    a bad credential makes that server fail closed rather than leaking to the
    client. An empty result is replaced with public STUN defaults by callers.
    """
    result: List[dict] = []
    for server in servers or ():
        if len(result) >= MAX_ICE_SERVERS:
            break
        raw_urls = getattr(server, "urls", ()) or ()
        if isinstance(raw_urls, str):
            raw_urls = [raw_urls]
        urls: List[str] = []
        for raw in raw_urls:
            try:
                candidate = validate_ice_url(raw)
            except ValueError:
                continue
            if candidate not in urls:
                urls.append(candidate)
            if len(urls) >= MAX_ICE_URLS_PER_SERVER:
                break
        if not urls:
            continue
        username = _bounded_credential(getattr(server, "username", None), "username")
        credential = _bounded_credential(
            getattr(server, "credential", None), "credential"
        )
        result.append({"urls": urls, "username": username, "credential": credential})
    return result


# --- session/participant metadata (commit 6) ---
MAX_SESSION_ID_LENGTH = 128
MAX_SFU_ID_LENGTH = 128
MAX_PARTICIPANTS_PER_CHANNEL = 100

_INVISIBLE_ID_CHARS = frozenset(
    {
        0x200B,
        0x200C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
        0xFEFF,
    }
)


def validate_text_identifier(value: object, field: str, max_length: int) -> str:
    """Return a bounded, control-free text identifier."""
    text = str(value or "")
    if len(text) > max_length:
        raise ValueError(f"{field} exceeds maximum length of {max_length}")
    for ch in text:
        code = ord(ch)
        if code < 0x20 or code == 0x7F or code in _INVISIBLE_ID_CHARS:
            raise ValueError(f"{field} contains invalid characters")
    return text


def validate_session_id(value: object, field: str = "session_id") -> str:
    """Validate a voice session id returned to clients or stored on a connection."""
    return validate_text_identifier(value, field, MAX_SESSION_ID_LENGTH)


def validate_sfu_identifier(value: object, field: str = "sfu_id") -> str:
    """Validate an SFU-scoped room/peer/transport identifier."""
    return validate_text_identifier(value, field, MAX_SFU_ID_LENGTH)


def _coerce_int(value: object) -> Optional[int]:
    """Return ``value`` as an int when it is an int-ish scalar, else ``None``.

    ``int(value)`` on an arbitrary object both truncates floats and accepts
    ``bool`` as 0/1, so the clamps below must not use it directly; narrowing on
    the type first is also what lets a type checker see the conversion.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, str)):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def clamp_user_limit(value: object, field: str = "user_limit") -> int:
    """Clamp a stored or supplied channel user limit into a sane range."""
    number = _coerce_int(value)
    if number is None:
        return 0
    return max(0, min(number, MAX_PARTICIPANTS_PER_CHANNEL))


def clamp_participant_count(value: object) -> int:
    """Clamp a participant count reported to clients."""
    number = _coerce_int(value)
    if number is None:
        return 0
    return max(0, min(number, MAX_PARTICIPANTS_PER_CHANNEL))


# --- region/bitrate enums and numeric ranges (commit 7) ---
MIN_BITRATE = 8_000
MAX_BITRATE = 384_000
# A value beyond a 32-bit signed int is a typo/abuse, not a bitrate; reject it
# before the existing clamp so it never reaches a SQL parameter.
MAX_BITRATE_INPUT = 2**31 - 1

VALID_REGION_IDS = frozenset(
    {
        "us-west",
        "us-east",
        "us-central",
        "us-south",
        "eu-west",
        "eu-central",
        "singapore",
        "japan",
        "brazil",
        "sydney",
        "automatic",
    }
)
MAX_REGION_ID_LENGTH = 64


def validate_bitrate_input(value: object, field: str = "bitrate") -> int:
    """Coerce a caller-supplied bitrate to an int in a safe input range.

    The manager still clamps to ``MIN_BITRATE..MAX_BITRATE``; this guard only
    rejects non-numeric and absurd values (including bools/floats) first.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{field} must be an integer")
    number = _coerce_int(value)
    if number is None:
        raise ValueError(f"{field} must be an integer")
    if number < 0 or number > MAX_BITRATE_INPUT:
        raise ValueError(f"{field} is outside the allowed range")
    return number


def clamp_bitrate(value: object, field: str = "bitrate") -> int:
    """Return a bitrate clamped to the supported audio range."""
    number = _coerce_int(value)
    if number is None:
        return MIN_BITRATE
    return max(MIN_BITRATE, min(number, MAX_BITRATE))


def validate_region_id(value: object | None, field: str = "region_id") -> str | None:
    """Validate a voice region against the known region enum.

    ``None`` (automatic/default) is allowed; any other value must be a bounded,
    control-free id present in :data:`VALID_REGION_IDS`.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > MAX_REGION_ID_LENGTH:
        raise ValueError(f"{field} is empty or too long")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise ValueError(f"{field} contains control characters")
    if text not in VALID_REGION_IDS:
        raise ValueError(f"Invalid region: {text}")
    return text


def validate_quality_metrics(metrics: dict, field: str = "metrics") -> dict:
    """Bound numeric quality samples before they reach the quality store."""
    result = {}
    for key, value in metrics.items():
        if value is None:
            continue
        if key == "bitrate":
            result[key] = clamp_bitrate(value)
        elif key in {"packet_loss", "jitter", "round_trip_time"}:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number != number or number in (float("inf"), float("-inf")):
                continue
            result[key] = max(0.0, min(number, 100_000.0))
        elif key == "score":
            # mediasoup rates a producer/consumer 0-10; the score is part of the
            # bridge contract (the quality store records it alongside the
            # transport metrics), so dropping it degraded every sample to a
            # hint with no verdict attached.
            number = _coerce_int(value)
            if number is None:
                continue
            result[key] = max(0, min(number, 10))
        elif key == "quality_level":
            text = str(value).lower()
            if text in {"excellent", "good", "fair", "poor", "critical"}:
                result[key] = text
    return result


# --- transcription / recording payload boundaries (commit 8) ---
MAX_TRANSCRIPT_TEXT_LENGTH = 1_000_000
MAX_TRANSCRIPT_SEGMENTS = 10_000
MAX_RECORDING_ID_LENGTH = 128
MAX_TRANSCRIPTION_CONFIG_STRING = 256


def validate_recording_id(value: object, field: str = "recording_id") -> str:
    """Validate a recording id before it is used in a filepath or response."""
    text = str(value or "").strip()
    if not text or len(text) > MAX_RECORDING_ID_LENGTH:
        raise ValueError(f"{field} is empty or too long")
    for ch in text:
        code = ord(ch)
        if code < 0x20 or code == 0x7F or ch in "/\\..":
            raise ValueError(f"{field} contains invalid characters")
    return text


def validate_transcription_config(cfg: object) -> dict:
    """Bound the config dict passed to the transcription worker."""
    if not isinstance(cfg, dict):
        raise ValueError("transcription config must be an object")
    result: dict = {}
    for key in ("provider", "language", "model", "model_size"):
        if key in cfg and cfg[key] is not None:
            text = str(cfg[key]).strip()
            if not text or len(text) > MAX_TRANSCRIPTION_CONFIG_STRING:
                raise ValueError(f"transcription config {key} is invalid")
            if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
                raise ValueError(f"transcription config {key} contains control chars")
            result[key] = text
    for key in ("max_duration_seconds", "chunk_seconds", "max_segments"):
        if key in cfg and cfg[key] is not None:
            result[key] = bounded_int(cfg[key], key, minimum=1, maximum=86_400)
    return result


def validate_transcription_payload(payload: object) -> dict:
    """Bound a transcription result before it is persisted or returned."""
    if not isinstance(payload, dict):
        raise ValueError("transcription payload must be an object")
    segments = payload.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("transcription segments must be a list")
    if len(segments) > MAX_TRANSCRIPT_SEGMENTS:
        raise ValueError("transcription has too many segments")

    total = 0
    valid_segments = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("transcription segment must be an object")
        text = str(segment.get("text", "") or "")
        if len(text) > MAX_TRANSCRIPT_TEXT_LENGTH:
            raise ValueError("transcript segment is too large")
        total += len(text)
        if total > MAX_TRANSCRIPT_TEXT_LENGTH:
            raise ValueError("transcript payload is too large")
        valid_segments.append({"text": text})

    result = {"segments": valid_segments, "total_chars": total}
    if payload.get("call_id") is not None:
        result["call_id"] = parse_id(payload["call_id"], "call_id")
    if payload.get("recording_id") is not None:
        result["recording_id"] = validate_recording_id(payload["recording_id"])
    return result


# --- audio processing parameter ranges (commit 9) ---
MIN_CRF = 0
MAX_CRF = 63
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 192_000
VALID_SAMPLE_RATES = frozenset({8000, 12000, 16000, 24000, 44100, 48000, 96000, 192000})
MIN_FRAME_DURATION_MS = 5
MAX_FRAME_DURATION_MS = 100


def _strict_bounded_int(value: object, field: str, minimum: int, maximum: int) -> int:
    """bounded_int plus rejection of bools and fractional floats."""
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError(f"{field} must be an integer")
    return bounded_int(value, field, minimum=minimum, maximum=maximum)


def validate_crf(value: object, field: str = "crf") -> int:
    """Validate an encoder CRF (0 = lossless, 63 = lowest quality)."""
    return _strict_bounded_int(value, field, MIN_CRF, MAX_CRF)


def validate_sample_rate(value: object, field: str = "sample_rate") -> int:
    """Validate an audio sample rate against the supported set/range."""
    rate = _strict_bounded_int(value, field, MIN_SAMPLE_RATE, MAX_SAMPLE_RATE)
    if rate not in VALID_SAMPLE_RATES:
        raise ValueError(f"{field} is not a supported sample rate")
    return rate


def validate_frame_duration_ms(value: object, field: str = "frame_duration_ms") -> int:
    """Validate an audio frame duration in milliseconds."""
    return _strict_bounded_int(
        value, field, MIN_FRAME_DURATION_MS, MAX_FRAME_DURATION_MS
    )


def validate_audio_profile(profile: object) -> dict:
    """Validate an audio encoding profile dict end-to-end."""
    if not isinstance(profile, dict):
        raise ValueError("audio profile must be an object")
    result: dict = {}
    if "sample_rate" in profile:
        result["sample_rate"] = validate_sample_rate(profile["sample_rate"])
    if "frame_duration_ms" in profile:
        result["frame_duration_ms"] = validate_frame_duration_ms(
            profile["frame_duration_ms"]
        )
    if "crf" in profile:
        result["crf"] = validate_crf(profile["crf"])
    return result
