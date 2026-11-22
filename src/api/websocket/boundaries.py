"""Inbound boundary validation for the gateway.

Every value that arrives on a WebSocket frame (or in a Join HTTP body) is
attacker-controlled JSON. This module is the one place where those values are
checked against the defense-in-depth helpers in
:mod:`src.core.security.hardening` before they can drive parsing, fan-out or
persistence.

The rules are deliberately conservative: an oversized, ill-typed or deeply
nested value is refused rather than being "fixed up" and allowed through. A
malformed client may lose a single frame; the server never pays for unbounded
parsing or allocation on its behalf.
"""

from __future__ import annotations

import json
import re
import threading
from collections import OrderedDict

from typing import Any, Dict

from .opcodes import GatewayOpcode
from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    reject_control_characters,
)

# --- Frame / message size bounds (commit 1) ---------------------------------

#: Absolute ceiling for a single inbound frame, independent of configuration.
#: An operator may configure a *smaller* limit; a larger one is clamped here so
#: a bad config entry cannot silently disable the guard.
MAX_FRAME_BYTES = 256 * 1024

#: Ceiling for the decompressed form of a single frame.
MAX_DECOMPRESSED_BYTES = 1024 * 1024

#: Structural bounds applied to an already-decoded frame. JSON parsing itself
#: is bounded by the byte ceilings above, but a small frame can still describe
#: a pathologically deep or wide object (which then blows up recursive helpers
#: such as ``stringify_snowflake_ids``), so the decoded shape is bounded too.
MAX_PARSED_DEPTH = 12
MAX_PARSED_KEYS = 128
MAX_PARSED_STRING_BYTES = 64 * 1024


def validate_frame_bytes(data: bytes) -> bool:
    """Whether a raw frame is small enough to parse.

    This is called on the raw ``text``/``bytes`` payload *before* ``json.loads``
    so a hostile frame is rejected without ever being decoded.
    """
    try:
        size = len(data)
    except TypeError:
        return False
    return 0 <= size <= MAX_FRAME_BYTES


def validate_parsed_structure(value: Any, _depth: int = 0) -> None:
    """Reject decoded frames whose shape would make downstream work unbounded.

    Raises :class:`HardeningViolation` when nesting depth, object width or an
    individual string value exceeds its ceiling. Containers are walked once;
    the function never mutates ``value``.
    """
    if _depth > MAX_PARSED_DEPTH:
        raise HardeningViolation("gateway frame nesting too deep")
    if isinstance(value, dict):
        if len(value) > MAX_PARSED_KEYS:
            raise HardeningViolation("gateway frame has too many keys")
        for key, item in value.items():
            if isinstance(key, str) and len(key.encode("utf-8", "ignore")) > 256:
                raise HardeningViolation("gateway frame key too long")
            validate_parsed_structure(item, _depth + 1)
    elif isinstance(value, (list, tuple)):
        if len(value) > MAX_PARSED_KEYS:
            raise HardeningViolation("gateway frame sequence too long")
        for item in value:
            validate_parsed_structure(item, _depth + 1)
    elif isinstance(value, str):
        if len(value.encode("utf-8", "ignore")) > MAX_PARSED_STRING_BYTES:
            raise HardeningViolation("gateway frame string too long")


def clamp_configured_limit(value: Any, field: str, ceiling: int, default: int) -> int:
    """Return a config-provided byte limit clamped into ``[1, ceiling]``."""
    return bounded_int(value, field, minimum=1, maximum=ceiling, default=default)


def decode_gateway_frame(raw: bytes) -> dict:
    """Decode one raw text frame into a bounded JSON object.

    Combines the pre-parse byte ceiling, UTF-8 decoding, JSON parsing, the
    object-shape requirement and the structural bound into one call so a new
    receive path cannot accidentally skip one of them. Raises
    :class:`HardeningViolation` on any failure.
    """
    if not validate_frame_bytes(raw):
        raise HardeningViolation("gateway frame exceeds the byte ceiling")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HardeningViolation("gateway frame is not valid UTF-8") from exc
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HardeningViolation("gateway frame is not valid JSON") from exc
    if not isinstance(value, dict):
        raise HardeningViolation("gateway frame is not a JSON object")
    validate_parsed_structure(value)
    return value


# --- Aggregate inbound frame ceiling ---------------------------------------
# Per-opcode throttles (``handlers.INBOUND_RATE_LIMITS``) cap the expensive work
# done by each opcode, and the completeness guard keeps that table exhaustive
# for *routed* opcodes. They do not, however, bound the overall NUMBER of frames
# a single connection may push in a window: soft-rejected opcodes have no
# per-opcode ceiling, and even fully throttled ones can be rotated to sum their
# budgets. Every frame still costs a byte-limit check, UTF-8 decode,
# ``json.loads`` and a recursive structural walk before it is dropped, so an
# unbounded count is an unbounded CPU/log amplifier regardless of the per-frame
# size cap. This is the only ceiling that applies to EVERY inbound frame.
#
# The budget is deliberately generous (well above the sum of all per-opcode
# budgets) so legitimate aggregate traffic is unaffected; over-budget frames
# are dropped fail-closed, never admitted "just this once".
MAX_INBOUND_FRAMES_PER_CONNECTION = 1000
INBOUND_FRAME_WINDOW_S = 10.0
#: Reserved key in ``Connection._inbound_rate_buckets`` for the aggregate
#: budget. Opcodes are non-negative, so a negative key cannot collide with one.
GLOBAL_FRAME_BUCKET_KEY = -1


# --- Subscription caps (commit 3) ------------------------------------------

#: A single connection may hold at most this many artifact/workbook
#: subscriptions. Without a cap one socket can grow the registry without
#: limit and force every relay to fan out to thousands of phantom targets.
MAX_SUBSCRIPTIONS_PER_CONNECTION = 128


def subscription_allowed(existing_count: int, artifact_id: int, subscribed_ids) -> bool:
    """Whether a connection may add ``artifact_id`` to its subscription set.

    Re-subscribing to an id it already holds is always allowed (idempotent).
    """
    if artifact_id in subscribed_ids:
        return True
    return int(existing_count) < MAX_SUBSCRIPTIONS_PER_CONNECTION


# --- Queued operation bounds (commit 4) -----------------------------------
#: Upper bound for a single relayed/persisted operation envelope.
MAX_OP_PAYLOAD_BYTES = 256 * 1024

#: Upper bound for a replay/batch of operations returned in one frame.
MAX_OP_BATCH = 500

#: Upper bound for one event stored in a session's replay buffer.
MAX_REPLAY_EVENT_BYTES = 256 * 1024

#: Upper bound for the *total* serialized size of a session's replay buffer.
#: A per-event cap alone still allows a bounded-count buffer of near-ceiling
#: events (100 x 256 KiB = 25 MiB) to be replayed in one burst on resume. This
#: ceiling bounds the whole buffer so a single session cannot amplify into an
#: unbounded replay frame, including when its state is rehydrated from a
#: sibling worker (where events never passed through ``add_replay_event``).
MAX_SESSION_REPLAY_BYTES = 8 * 1024 * 1024


# --- Bounded memo of expensive payload serialization sizes -----------------
# ``payload_size`` is the byte guard for every replay frame. During session
# hydration/resume the SAME rendered event is measured once per session it is
# restored into (a broadcast frame lands in many sessions, and a reconnecting
# client's buffer is re-measured whenever its local cache is invalidated).
# Re-serializing a 48 KiB frame with ``json.dumps`` costs ~180 us; a bounded
# memo of the measured size collapses the Nth repeat to a fingerprint check.
#
# The memo NEVER replaces the real computation: a miss still runs the original
# ``json.dumps`` byte-for-byte, so the security bound is unchanged. Two guards
# keep it fail-closed:
#   * the entry is keyed by the measured object's identity, so a different
#     object can never inherit another's size; and
#   * a hit is served only when a cheap structural fingerprint proves the
#     object still serializes to exactly the same bytes (in-place mutation is
#     detected). Values the fingerprint cannot prove (custom objects routed
#     through ``default=str``, tuples, non-string keys, oversized keys) are
#     never memoized. Cheap payloads below ``PAYLOAD_SIZE_MEMO_MIN_BYTES`` are
#     always re-measured: validating them costs more than the serialization.
MAX_PAYLOAD_SIZE_CACHE_ENTRIES = 128
PAYLOAD_SIZE_MEMO_MIN_BYTES = 2048
#: Cap the characters retained by one fingerprint. A single replay frame is
#: already bounded by ``MAX_REPLAY_EVENT_BYTES``; matching that ceiling lets
#: every admissible frame be memoized while bounding key memory/hash cost.
MAX_PAYLOAD_SIZE_KEY_CHARS = MAX_REPLAY_EVENT_BYTES

_UNCACHEABLE_SIZE_KEY = object()
_MEMO_MISSING = object()


def _size_cache_key(value: Any):
    """Return ``(fingerprint, cost)`` or ``(_UNCACHEABLE_SIZE_KEY, 0)``.

    Two values with equal fingerprints serialize to identical JSON bytes under
    ``payload_size`` (same types, keys, string contents and container order),
    so a cached size stays valid. Strings are held by reference, not copied:
    comparing an unchanged key is O(1) per string thanks to CPython's identity
    shortcut. Anything whose serialization the fingerprint cannot prove is
    reported uncacheable so the real measurement is always used.
    """
    if value is None:
        return ("null",), 0
    kind = type(value)
    if kind is bool:
        return ("bool", value), 0
    if kind is int:
        return ("int", value), 0
    if kind is float:
        # NaN never compares equal, so it naturally misses the cache; Infinity
        # compares equal and serializes identically, so it is safe to cache.
        return ("float", value), 0
    if kind is str:
        cost = len(value)
        if cost > MAX_PAYLOAD_SIZE_KEY_CHARS:
            return _UNCACHEABLE_SIZE_KEY, 0
        return ("str", value), cost
    if kind is dict:
        parts = []
        cost = 0
        for key, item in value.items():
            # json.dumps coerces non-string keys; refusing them here keeps the
            # fingerprint an exact statement about the serialized bytes.
            if type(key) is not str:
                return _UNCACHEABLE_SIZE_KEY, 0
            sub, sub_cost = _size_cache_key(item)
            if sub is _UNCACHEABLE_SIZE_KEY:
                return _UNCACHEABLE_SIZE_KEY, 0
            cost += len(key) + sub_cost
            if cost > MAX_PAYLOAD_SIZE_KEY_CHARS:
                return _UNCACHEABLE_SIZE_KEY, 0
            parts.append((key, sub))
        return ("dict", tuple(parts)), cost
    if kind is list:
        parts = []
        cost = 0
        for item in value:
            sub, sub_cost = _size_cache_key(item)
            if sub is _UNCACHEABLE_SIZE_KEY:
                return _UNCACHEABLE_SIZE_KEY, 0
            cost += sub_cost
            if cost > MAX_PAYLOAD_SIZE_KEY_CHARS:
                return _UNCACHEABLE_SIZE_KEY, 0
            parts.append(sub)
        return ("list", tuple(parts)), cost
    # Tuples, sets, custom objects (default=str), etc. cannot be proven cheaply.
    return _UNCACHEABLE_SIZE_KEY, 0


class _PayloadSizeMemo:
    """Thread-safe bounded LRU of ``id(value) -> (value, size, fingerprint)``.

    The strong reference pins the identity (so ``id()`` cannot be recycled) and
    is checked on lookup. Only expensive, provably-JSON values carry a
    fingerprint and can be served from the memo.
    """

    def __init__(self, max_entries: int = MAX_PAYLOAD_SIZE_CACHE_ENTRIES) -> None:
        self._max_entries = max(1, int(max_entries))
        self._entries: "OrderedDict[int, tuple]" = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def lookup(self, value: Any) -> Any:
        """Return the live entry for ``value`` (or ``None``), counting a miss."""
        with self._lock:
            entry = self._entries.get(id(value), _MEMO_MISSING)
            if entry is _MEMO_MISSING or entry[0] is not value:
                self._misses += 1
                return None
            self._entries.move_to_end(id(value))
            return entry

    def store(self, value: Any, size: int, fingerprint: Any) -> None:
        with self._lock:
            self._entries[id(value)] = (value, size, fingerprint)
            self._entries.move_to_end(id(value))
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def record_hit(self) -> None:
        """Record a served hit. ``lookup`` only counts a miss when there is no
        live entry, so this call must not decrement ``_misses`` (doing so drove
        the observability counter negative on every hit).
        """
        with self._lock:
            self._hits += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "size": len(self._entries),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
            }


_payload_size_memo = _PayloadSizeMemo()


def get_payload_size_cache_stats() -> Dict[str, int]:
    """Expose hit/miss/size counters for observability and regression tests."""
    return _payload_size_memo.stats()


def clear_payload_size_cache() -> None:
    """Drop every memoized size (test isolation / config change)."""
    _payload_size_memo.clear()


def _measure_payload_size(value: object) -> int:
    """The original, unmodified byte computation (the security guard)."""
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return MAX_OP_PAYLOAD_BYTES + 1


def _safe_size_key(value: object):
    """Fingerprint or the uncacheable sentinel; never propagate a walk error.

    A pathologically nested payload makes both ``json.dumps`` and the recursive
    walk fail. The old code failed closed with an oversized size; keep that
    behaviour instead of turning the memo into a new exception source.
    """
    try:
        key, _ = _size_cache_key(value)
        return key
    except (TypeError, ValueError, RecursionError):
        return _UNCACHEABLE_SIZE_KEY


def payload_size(value: object) -> int:
    """Serialized byte size of a payload; oversized/unserializable -> too big.

    Uses ``default=str`` so an unusual but JSON-friendly object is still
    measurable; anything that cannot be serialized is treated as exceeding
    the cap rather than allowed through. Expensive serializations reuse a
    bounded memo after a structural validation; on a miss the original
    computation runs in full, so the byte guard is never weakened.
    """
    entry = _payload_size_memo.lookup(value)
    if entry is not None:
        _, size, fingerprint = entry
        if size > PAYLOAD_SIZE_MEMO_MIN_BYTES and fingerprint is not None:
            key = _safe_size_key(value)
            if key is not _UNCACHEABLE_SIZE_KEY and key == fingerprint:
                _payload_size_memo.record_hit()
                return size

    size = _measure_payload_size(value)
    if size > PAYLOAD_SIZE_MEMO_MIN_BYTES:
        key = _safe_size_key(value)
        if key is not _UNCACHEABLE_SIZE_KEY:
            _payload_size_memo.store(value, size, key)
    return size


def bounded_batch(value: object, default: int = MAX_OP_BATCH) -> int:
    """Coerce a replay/batch limit into ``[1, MAX_OP_BATCH]``."""
    return bounded_int(
        value, "batch size", minimum=1, maximum=MAX_OP_BATCH, default=default
    )


# --- Snowflake ids (commit 5) ---------------------------------------------
def parse_snowflake(value: object, field: str = "id") -> int:
    """Parse an id in ``[1, 2**63-1]`` from an untrusted gateway payload.

    Bare ``int()`` accepted negatives and oversized values that then flowed
    into repository lookups, replay offsets and delta fan-out. Delegates to
    the shared range guard so the rule stays identical across boundaries.
    """
    return parse_id(value, field)


# --- Presence / status payloads (commit 6) -------------------------------
MAX_STATUS_LEN = 32
MAX_CUSTOM_STATUS_LEN = 128
MAX_EMOJI_LEN = 64
MAX_ACTIVITY_TYPE_LEN = 64
MAX_ACTIVITY_NAME_LEN = 128
MAX_ACTIVITY_TEXT_LEN = 256
MAX_ACTIVITIES = 5


def _bounded_text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise HardeningViolation(f"{field} must be a string")
    text = reject_control_characters(value, field)
    if len(text) > limit:
        raise HardeningViolation(f"{field} exceeds {limit} characters")
    return text


def validate_presence_payload(data: object) -> dict:
    """Validate and normalize a PRESENCE_UPDATE payload.

    Status/custom-status/emoji/activity text is attacker-controlled and was
    copied into persisted rows and broadcast frames without limits. Control
    characters are refused and every field is length-bounded; the returned
    dict carries only validated values for the handler to use.
    """
    if not isinstance(data, dict):
        raise HardeningViolation("presence payload must be an object")

    status = _bounded_text(data.get("status", "online"), "status", MAX_STATUS_LEN)

    custom_status = None
    if data.get("custom_status") is not None:
        custom_status = _bounded_text(
            data["custom_status"], "custom_status", MAX_CUSTOM_STATUS_LEN
        )
    custom_emoji = None
    if data.get("custom_emoji") is not None:
        custom_emoji = _bounded_text(
            data["custom_emoji"], "custom_emoji", MAX_EMOJI_LEN
        )

    activities = data.get("activities", [])
    if not isinstance(activities, list):
        raise HardeningViolation("activities must be a list")
    if len(activities) > MAX_ACTIVITIES:
        raise HardeningViolation("too many activities")
    clean_activities = []
    for activity in activities:
        if not isinstance(activity, dict):
            raise HardeningViolation("activity must be an object")
        # The activity type was the one attacker-controlled string copied
        # verbatim into the normalized payload. It is later interpolated into
        # warning logs (on an invalid enum value) and into the presence event
        # sent to every friend, so it must be a bounded, control-character-free
        # string just like every other activity field.
        # Coerce to text first: clients may send a numeric activity type and
        # the enum conversion happens in the handler, not here. What matters
        # at the boundary is that the value cannot carry control characters
        # into logs/broadcasts or grow without limit.
        clean = {
            "type": _bounded_text(
                str(activity.get("type", "custom")), "type", MAX_ACTIVITY_TYPE_LEN
            )
        }
        for key, limit in (
            ("name", MAX_ACTIVITY_NAME_LEN),
            ("details", MAX_ACTIVITY_TEXT_LEN),
            ("state", MAX_ACTIVITY_TEXT_LEN),
        ):
            if activity.get(key) is not None:
                clean[key] = _bounded_text(activity[key], key, limit)
        clean_activities.append(clean)

    return {
        "status": status,
        "custom_status": custom_status,
        "custom_emoji": custom_emoji,
        "activities": clean_activities,
    }


# --- Mutating opcode authentication (commit 7) ----------------------------
#: Opcodes that change server-side state or cause a fan-out. IDENTIFY/RESUME
#: (which establish authentication) and HEARTBEAT (liveness only) are
#: deliberately absent. Resource-level authorization remains delegated to the
#: owning manager inside each handler; this set guarantees the *identity*
#: gate is applied uniformly before any of them can run.
MUTATING_OPCODES = frozenset(
    int(op)
    for op in (
        GatewayOpcode.PRESENCE_UPDATE,
        GatewayOpcode.VOICE_STATE_UPDATE,
        GatewayOpcode.REQUEST_GUILD_MEMBERS,
        GatewayOpcode.VOICE_CONNECT,
        GatewayOpcode.VOICE_DISCONNECT,
        GatewayOpcode.VOICE_SDP_OFFER,
        GatewayOpcode.VOICE_SDP_ANSWER,
        GatewayOpcode.VOICE_ICE_CANDIDATE,
        GatewayOpcode.VOICE_SPEAKING,
        GatewayOpcode.VOICE_QUALITY,
        GatewayOpcode.TYPING_START,
        GatewayOpcode.TYPING_STOP,
        GatewayOpcode.ARTIFACT_SUBSCRIBE,
        GatewayOpcode.ARTIFACT_UNSUBSCRIBE,
        GatewayOpcode.ARTIFACT_OP,
        GatewayOpcode.SUM_SUBSCRIBE,
        GatewayOpcode.SUM_UNSUBSCRIBE,
        GatewayOpcode.SUM_OP,
        GatewayOpcode.INTERACTION_CREATE,
        GatewayOpcode.INTERACTION_RESPONSE,
        GatewayOpcode.RATCHET_UPDATE,
    )
)


def requires_authentication(opcode: int) -> bool:
    """Whether ``opcode`` mutates state and therefore needs an identified user."""
    return int(opcode) in MUTATING_OPCODES


# --- Reconnect / ticket bounds (commit 8) ---------------------------------
#: Tickets are machine tokens, never free-form text: no whitespace, no C0
#: controls, and a bounded length. The character class covers the URL-safe
#: alphabet plus the separators used by existing token formats.
TICKET_PATTERN = re.compile(r"^[A-Za-z0-9._~+/:=@-]+$")
MIN_TICKET_LEN = 1
MAX_TICKET_LEN = 4096
MAX_SESSION_ID_LEN = 128
SESSION_ID_PATTERN = re.compile(r"^[0-9a-fA-F]+$")
MAX_RESUME_SEQUENCE = 1_000_000
MIN_BACKOFF_MS = 0
MAX_BACKOFF_MS = 300_000
MAX_PROPERTY_LEN = 256
MAX_PROPERTIES = 16


def validate_ticket(value: object, field: str = "token") -> str:
    """Return ``value`` as a well-formed, bounded auth ticket or raise."""
    if not isinstance(value, str):
        raise HardeningViolation(f"{field} must be a string")
    text = reject_control_characters(value, field)
    if not (MIN_TICKET_LEN <= len(text) <= MAX_TICKET_LEN):
        raise HardeningViolation(f"{field} length is out of range")
    # ``fullmatch`` rather than ``match`` + ``$``: Python's ``$`` also matches
    # just before a trailing newline. ``reject_control_characters`` already
    # strips that vector, but anchoring the whole value here keeps the rule
    # correct even if the pattern or helper changes later.
    if not TICKET_PATTERN.fullmatch(text):
        raise HardeningViolation(f"{field} has an invalid format")
    return text


def validate_session_id(value: object) -> str:
    """Validate a resume session id (hex token of bounded length)."""
    if not isinstance(value, str) or not (1 <= len(value) <= MAX_SESSION_ID_LEN):
        raise HardeningViolation("session_id length is out of range")
    text = reject_control_characters(value, "session_id")
    # ``match`` + ``$`` accepted a trailing newline ("deadbeef\n") because
    # Python's ``$`` matches before a final ``\n``. That value flowed into
    # Valkey session keys and log lines. ``fullmatch`` consumes the entire
    # string, so no trailing/embedded separator can slip through.
    if not SESSION_ID_PATTERN.fullmatch(text):
        raise HardeningViolation("session_id has an invalid format")
    return text


def bounded_resume_sequence(value: object) -> int:
    """Bound a client-supplied resume sequence into a sane replay window."""
    if isinstance(value, bool):
        raise HardeningViolation("seq must be an integer")
    return bounded_int(
        value,
        "seq",
        minimum=0,
        maximum=MAX_RESUME_SEQUENCE,
        default=0,
    )


def bounded_backoff(value: object, field: str = "backoff") -> int:
    """Bound a reconnect/backoff duration in milliseconds."""
    if isinstance(value, bool):
        raise HardeningViolation(f"{field} must be an integer")
    return bounded_int(
        value, field, minimum=MIN_BACKOFF_MS, maximum=MAX_BACKOFF_MS, default=0
    )


def validate_properties(value: object) -> dict:
    """Bound the IDENTIFY ``properties`` map without changing valid values."""
    if not isinstance(value, dict):
        raise HardeningViolation("properties must be an object")
    if len(value) > MAX_PROPERTIES:
        raise HardeningViolation("too many client properties")
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > MAX_PROPERTY_LEN:
            raise HardeningViolation("property key is too long")
        if isinstance(item, str) and len(item) > MAX_PROPERTY_LEN:
            raise HardeningViolation("property value is too long")
    return value
