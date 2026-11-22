"""
DM Spam Detector - Anti-spam heuristics for direct messages.

Provides rate limiting, duplicate message detection, and pattern-based
spam filtering for DM conversations to protect users from harassment and spam.

Counter state lives in an atomic record-and-count window primitive with
three tiers, all of which stay correct under ``--scale backend=N``:

1. Valkey INCR+EXPIRE Lua script (preferred): one shared counter per key.
2. A shared, windowed SQL table when Valkey is unreachable: every worker
   touches the same row, so the limit is still enforced GLOBALLY (a local
   dict here would let N workers each spend the full budget, i.e. Nx spam).
3. A bounded process-local dict only when the shared database is also
   unreachable. Degradation is logged once (see ``_warn_valkey_down``).
"""

import fnmatch
import hashlib
import re
import threading
import unicodedata
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import utils.logger as logger
from src.core.base import timestamp_ms, generate_id
from src.core.security.hardening import HardeningViolation, bounded_int
from src.core.database.valkey_client import (
    get_client,
    is_available,
    ValkeyOperationError,
)


# Atomic INCR + first-hit EXPIRE. Doing this in Lua closes the race where a
# process died (or EXPIRE failed) between INCR and EXPIRE: previously the
# counter then persisted with NO TTL and the sender was rate-limited
# permanently.
_INCR_EXPIRE_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
    redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return current
"""

_LOCAL_WINDOWS: Dict[str, List[float]] = {}
_LOCAL_WINDOWS_LOCK = threading.Lock()
_LOCAL_WINDOW_MAX_KEYS = 50_000
_VALKEY_DOWN_WARNED = False

# Tier 2: shared SQL counters. ``dm_spam_counters`` is created by the dmspam
# schema; the lazy-create guard below also covers installs whose initial
# migration predates the table. Rows carry a fixed-window start (TTL-like
# expiry via the sweep) and the table is hard-capped so a key flood cannot
# grow it without bound.
_SHARED_COUNTER_TABLE_READY = False
_SHARED_COUNTER_SWEEP_AT = 0.0
_SHARED_COUNTER_MAX_ROWS = 50_000
_SHARED_COUNTER_SWEEP_INTERVAL_S = 60.0
_SHARED_COUNTER_LOCK = threading.Lock()

# Recipient-configured burst parameters come from a JSON/store row and were
# previously coerced with bare int(): a threshold of 10**9 with a 10**9-second
# window makes the local counter grow without bound, and a multi-megabyte
# content string is canonicalised/scanned on every message. Bound all of them.
MAX_DM_CONTENT_LENGTH = 10_000
MAX_FILTER_THRESHOLD = 10_000
MAX_FILTER_WINDOW_SECONDS = 604_800  # 7 days
MAX_FILTER_PATTERN_LENGTH = 512
DM_FILTER_TYPES = frozenset({"rate", "duplicate", "pattern"})
DM_FILTER_ACTIONS = frozenset({"warn", "block"})


def _bounded_filter_int(value, field, minimum, maximum, default):
    """Return a bounded config integer, falling back to ``default``."""
    try:
        return bounded_int(
            value, field, minimum=minimum, maximum=maximum, default=default
        )
    except HardeningViolation:
        return default


def _has_glob_redos_risk(pattern: str) -> bool:
    """Flag stored globs whose ``fnmatch`` translation backtracks super-linearly.

    ``fnmatch`` turns every ``*`` into a greedy ``.*``. A single quantifier can
    only backtrack linearly, but two or more form a chain whose split count is
    exponential in the number of ``*`` groups: ``*a*a*...*b`` on a run of
    ``a`` characters with no ``b`` is the classic case. Those shapes are routed
    to the linear matcher below; the rest keep the stdlib's exact semantics.
    """
    return pattern.count("*") >= 2


def _glob_segments(pattern: str) -> list[str]:
    """Split a glob on ``*`` wildcards that are outside ``[seq]`` classes.

    ``*`` is a literal member inside a bracket expression (``[*]``), so a
    plain ``pattern.split('*')`` would corrupt both the class and the wildcard
    layout. The class-boundary scan mirrors ``fnmatch._translate``: an optional
    leading ``!`` and ``]`` are members, and an unclosed ``[`` is a literal.
    """
    parts: list[str] = []
    current: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            parts.append("".join(current))
            current = []
            index += 1
            continue
        if char == "[":
            end = index + 1
            if end < length and pattern[end] == "!":
                end += 1
            if end < length and pattern[end] == "]":
                end += 1
            while end < length and pattern[end] != "]":
                end += 1
            if end >= length:  # unclosed class: fnmatch treats '[' literally
                current.append("[")
                index += 1
            else:
                current.append(pattern[index : end + 1])
                index = end + 1
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


@lru_cache(maxsize=256)
def _glob_segment_tokens(pattern: str) -> tuple:
    """Compile a stored glob's ``*``-separated segment matchers once.

    ``_linear_glob_match`` runs for every recipient pattern-filter row on
    every scanned message.  The row is recipient configuration; its segment
    regexes are a pure function of the (length-bounded) pattern string and
    are identical for every message.  Rebuilding them on each call was pure
    repeated work on the send path.  Cache the immutable tuple of compiled
    patterns (``None`` marks a single ``*`` wildcard) by the pattern string.

    The ``maxsize`` bound keeps rotating/hostile patterns from pinning memory;
    the key is the caller-bounded pattern (``_safe_fnmatchcase`` rejects
    patterns longer than ``MAX_FILTER_PATTERN_LENGTH`` before the matcher is
    reached).  Nothing user-specific is shared: a cached value depends only on
    the pattern's own text, so no authz decision or per-recipient state is
    involved.  Compiled patterns are immutable and their ``match`` is
    thread-safe.
    """
    tokens: list[Any] = []
    for index, part in enumerate(_glob_segments(pattern)):
        # Consecutive ``*`` collapse, exactly as fnmatch's translate does.
        if index:
            tokens.append(None)  # a single ``*`` wildcard
        if part:
            # Drop fnmatch's trailing ``\Z`` so a segment can be matched at an
            # arbitrary offset; ``Match.end()`` then reports exactly how many
            # characters the segment consumed (a ``[seq]`` class or ``?``
            # consumes one character, not its raw source length).
            translated = fnmatch.translate(part)
            # 3.14 emits ``\z``; older releases emit ``\Z``.
            translated = translated.removesuffix(r"\z").removesuffix(r"\Z")
            tokens.append(re.compile(translated))
    return tuple(tokens)


def _linear_glob_match(name: str, pattern: str) -> bool:
    """Match a glob in linear time without a backtracking quantifier chain.

    The pattern is split into ``*``-separated fixed-width segments. Each
    segment is validated with ``fnmatch``'s own translation, which for a
    ``*``-free segment contains only literals, ``?`` and ``[seq]`` single
    character classes -- no ``.*`` -- so matching one segment is linear.
    Segments are then aligned with the classic greedy two-pointer wildcard
    algorithm: on a mismatch only the most recent ``*`` is advanced, so a
    stored pattern of up to ``MAX_FILTER_PATTERN_LENGTH`` chars against a
    bounded canonical message cannot blow up exponentially.  The compiled
    segment matchers are cached per pattern; the linear two-pointer algorithm
    below is unchanged, so the ReDoS protection is preserved.
    """
    tokens = _glob_segment_tokens(pattern)

    token_i = 0
    name_i = 0
    star_token = -1
    star_name = 0
    token_count = len(tokens)
    name_len = len(name)

    while name_i < name_len:
        if token_i < token_count and tokens[token_i] is not None:
            segment = tokens[token_i].match(name, name_i)
            if segment is not None:
                token_i += 1
                name_i = segment.end()
                continue
        elif token_i < token_count:  # a ``*``: remember the best split point
            star_token = token_i
            star_name = name_i
            token_i += 1
            continue
        # Mismatch or tokens exhausted: advance the last ``*`` by one char.
        if star_token >= 0:
            token_i = star_token + 1
            star_name += 1
            name_i = star_name
        else:
            return False

    # Trailing ``*`` wildcards may absorb nothing.
    while token_i < token_count and tokens[token_i] is None:
        token_i += 1
    return token_i == token_count


def _safe_fnmatchcase(name: str, pattern: str) -> bool:
    """fnmatch-equivalent glob matching with a hard bound on backtracking.

    Recipient-stored DM filters are attacker-controlled and reloaded on every
    worker start, so they must never reach a matcher whose cost depends on
    how a long message can be partitioned around ``*`` wildcards.
    """
    if len(pattern) > MAX_FILTER_PATTERN_LENGTH:
        return False
    if _has_glob_redos_risk(pattern):
        return _linear_glob_match(name, pattern)
    # Zero or one ``*``: fnmatch's single ``.*`` backtracks at most linearly.
    return fnmatch.fnmatchcase(name, pattern)


# H6: canonicalization for all user-controlled matching/hashing/counting.
# Strict ``==``/exact-byte comparisons let trivial variants (trailing space,
# case change, fullwidth Unicode, zero-width joiners) evade blocking, so every
# content and pattern comparison below goes through _canonical_text first:
# NFKC (folds fullwidth/compat forms to ASCII), casefold (stronger than
# lower()), invisible/control char removal, whitespace-run collapsing, trim.
_WS_RUN_PATTERN = re.compile(r"\s+")

# Unicode general categories that are visually invisible but still change the
# bytes of a message. Cf = format (zero-width joiners, bidi overrides,
# direction marks, word joiners, soft hyphen, BOM, ...), Cc = control
# (C0/C1 including NUL). A sender can splice these anywhere into spam to make
# the canonical hash/glob/mention scan differ on every send while the text
# looks identical to the recipient. Removing every Cf/Cc char (rather than a
# fixed 5-character denylist) closes that whole class of normalization
# bypasses, not just the handful originally enumerated.
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc"})


def _strip_invisible(text: str) -> str:
    """Drop invisible format/control characters from ``text``."""
    return "".join(
        ch for ch in text if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
    )


def _canonical_text(content: Any) -> str:
    """Canonicalize text before matching, hashing, or counting.

    Besides folding case/width/whitespace, this strips every invisible format
    and control character and enforces a hard length bound *after* NFKC. The
    latter matters because NFKC can expand a single compatibility character
    into many (e.g. U+FDFA -> 18 codepoints); without re-truncating, the
    pre-canonicalization cap in ``_check_message`` can be amplified ~18x and
    make every hash/glob/indicator scan work on an unbounded string.

    Invisible characters are removed *before* whitespace is collapsed so that
    an invisible next to a space cannot leave behind a doubled whitespace run
    (a normalization bypass that otherwise changes the canonical hash).
    """
    if content is None:
        return ""
    if not isinstance(content, str):
        content = str(content)
    text = unicodedata.normalize("NFKC", content)
    text = text.casefold()
    # Strip invisible format/control characters BEFORE collapsing whitespace.
    # The two steps do not commute: an invisible code point (e.g. U+200B) is
    # not \\s, so "buy <ZWSP> crypto" survives the whitespace collapse and only
    # AFTERwards loses the ZWSP -- leaving a double space in the canonical
    # form. A sender could splice invisibles next to spaces to generate
    # arbitrarily many distinct hashes/globs for a visually identical message,
    # defeating duplicate counters and pattern filters. Removing invisibles
    # first lets the following whitespace collapse fold those runs back to the
    # single-space form a human actually sees.
    text = _strip_invisible(text)
    text = _WS_RUN_PATTERN.sub(" ", text)
    text = text.strip()
    if len(text) > MAX_DM_CONTENT_LENGTH:
        text = text[:MAX_DM_CONTENT_LENGTH]
    return text


def _is_unique_violation(exc: BaseException) -> bool:
    """Detect duplicate-key errors across sqlite/postgres drivers."""
    name = type(exc).__name__
    if name in ("IntegrityError", "UniqueViolation", "UniqueViolationError"):
        return True
    return "unique" in str(exc).lower()


def _row_dict(row: Any) -> Dict[str, Any]:
    """Normalize a DB row to a plain dict.

    The database layer normally returns dicts, but tuple-shaped rows
    (plain tuples with cursor.description available, sqlite3.Row, or
    objects) must not crash callers — see get_filters/_get_user_filters.
    """
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        try:
            return dict(row)
        except Exception as mapping_error:
            logger.debug(f"antispam row is not a mapping: {mapping_error}")
        try:
            return {k: row[k] for k in row.keys()}
        except Exception as keys_error:
            logger.debug(f"antispam row has no usable keys: {keys_error}")
    if isinstance(row, (list, tuple)):
        # Positional fallback for the dm_spam_filters column order
        # (id, user_id, target_user_id, pattern, filter_type, action,
        #  threshold, window_seconds, enabled, created_at, updated_at).
        keys = (
            "id",
            "user_id",
            "target_user_id",
            "pattern",
            "filter_type",
            "action",
            "threshold",
            "window_seconds",
            "enabled",
            "created_at",
            "updated_at",
        )
        return {k: (row[i] if i < len(row) else None) for i, k in enumerate(keys)}
    try:
        return dict(row)
    except Exception:
        return {}


def reset_local_windows() -> None:
    """Reset counter state (used on detector re-init / tests)."""
    global _SHARED_COUNTER_TABLE_READY, _SHARED_COUNTER_SWEEP_AT
    with _LOCAL_WINDOWS_LOCK:
        _LOCAL_WINDOWS.clear()
    with _SHARED_COUNTER_LOCK:
        _SHARED_COUNTER_TABLE_READY = False
        _SHARED_COUNTER_SWEEP_AT = 0.0


def _warn_valkey_down(detail: Any, tier: str) -> None:
    """Log the counter-store degradation exactly once until process restart."""
    global _VALKEY_DOWN_WARNED
    if _VALKEY_DOWN_WARNED:
        return
    _VALKEY_DOWN_WARNED = True
    logger.warning(
        f"DM spam counters: Valkey unavailable ({detail}); falling back to "
        f"{tier} - rate/duplicate limits stay enforced until Valkey recovers"
    )


class AtomicWindowCounter:
    """Atomic record-and-count fixed-window limiter.

    Tiers (see module docstring): a shared Valkey counter, then a shared
    windowed SQL counter, then a bounded process-local dict as a last resort.
    Only the last tier is per-worker; every other tier holds across
    ``--scale backend=N``.
    """

    def __init__(self) -> None:
        self._db: Optional[Any] = None

    def configure(self, db: Optional[Any]) -> None:
        """Bind the shared database used when Valkey is unreachable."""
        self._db = db

    def record_and_count(
        self, key: str, now_s: float, window_s: int, limit: int
    ) -> bool:
        """Record one occurrence and report whether it stays within ``limit``.

        The occurrence is ALWAYS recorded - including when it trips the limit -
        so blocked attempts keep applying pressure inside the window.
        """
        degraded_detail: Optional[BaseException] = None
        client = get_client()
        if client is not None and is_available():
            try:
                current = int(
                    client.eval_lua(_INCR_EXPIRE_SCRIPT, [key], [int(window_s)])
                )
                return current <= limit
            except ValkeyOperationError as e:
                degraded_detail = e

        # Valkey is down: never silently drop to a per-worker counter while a
        # shared store is still reachable. The SQL tier is bounded and swept,
        # so it cannot become an unbounded registry keyed by user/conversation.
        tier = "bounded process-local counters"
        if self._db is not None:
            try:
                result = self._record_shared(key, now_s, window_s, limit, self._db)
                tier = "shared SQL counters"
                if degraded_detail is not None:
                    _warn_valkey_down(degraded_detail, tier)
                return result
            except Exception as e:
                if degraded_detail is not None:
                    _warn_valkey_down(
                        f"{degraded_detail}; shared SQL counters failed: {e}", tier
                    )
                # A DB failure must not crash the DM send path; the local tier
                # keeps the pre-existing behaviour (no new fail-open).
        if degraded_detail is not None:
            _warn_valkey_down(degraded_detail, tier)
        return self._record_local(key, now_s, window_s, limit)

    @staticmethod
    def _ensure_shared_table(db: Any) -> None:
        """Create the counter table once, including on pre-table installs."""
        global _SHARED_COUNTER_TABLE_READY
        if _SHARED_COUNTER_TABLE_READY:
            return
        with _SHARED_COUNTER_LOCK:
            if _SHARED_COUNTER_TABLE_READY:
                return
            db.execute(
                "CREATE TABLE IF NOT EXISTS dm_spam_counters ("
                "id INTEGER PRIMARY KEY, "
                "cache_key TEXT NOT NULL, "
                "window_start INTEGER NOT NULL, "
                "count INTEGER NOT NULL DEFAULT 0, "
                "UNIQUE (cache_key, window_start))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_dm_spam_counter_window "
                "ON dm_spam_counters(window_start)"
            )
            _SHARED_COUNTER_TABLE_READY = True

    @staticmethod
    def _sweep_shared(db: Any, now_s: float) -> None:
        """Expire old windows and hard-cap the shared table (TTL + bound)."""
        global _SHARED_COUNTER_SWEEP_AT
        with _SHARED_COUNTER_LOCK:
            if now_s - _SHARED_COUNTER_SWEEP_AT < _SHARED_COUNTER_SWEEP_INTERVAL_S:
                return
            _SHARED_COUNTER_SWEEP_AT = now_s

        # A window never lives longer than the largest allowed config window.
        cutoff = int(now_s) - MAX_FILTER_WINDOW_SECONDS - 1
        db.execute("DELETE FROM dm_spam_counters WHERE window_start < ?", (cutoff,))
        # Oldest-first trim so a flood of distinct (user, conversation, hash)
        # keys cannot grow the table without limit. Delete only the EXCESS
        # rows; selecting from offset 0 would wipe every live counter.
        total = db.fetch_one("SELECT COUNT(*) AS n FROM dm_spam_counters")
        excess = (int(total["n"]) if total else 0) - _SHARED_COUNTER_MAX_ROWS
        if excess > 0:
            overflow = db.fetch_all(
                "SELECT id FROM dm_spam_counters "
                "ORDER BY window_start ASC, id ASC LIMIT ?",
                (excess,),
            )
            if overflow:
                ids = [row["id"] for row in overflow]
                placeholders = ",".join("?" for _ in ids)
                db.execute(
                    f"DELETE FROM dm_spam_counters WHERE id IN ({placeholders})",
                    tuple(ids),
                )

    @staticmethod
    def _record_shared(
        key: str, now_s: float, window_s: int, limit: int, db: Any
    ) -> bool:
        """Atomic UPDATE-first upsert against the shared SQL counter."""
        window_s = int(window_s)
        now = int(now_s)
        window_start = now - (now % window_s)

        AtomicWindowCounter._ensure_shared_table(db)

        update = (
            "UPDATE dm_spam_counters SET count = count + 1 "
            "WHERE cache_key = ? AND window_start = ?"
        )
        params = (key, window_start)
        cur = db.execute(update, params)
        if getattr(cur, "rowcount", 0) > 0:
            row = db.fetch_one(
                "SELECT count FROM dm_spam_counters "
                "WHERE cache_key = ? AND window_start = ?",
                params,
            )
            count = int(row["count"] if row else 1)
        else:
            try:
                db.execute(
                    "INSERT INTO dm_spam_counters "
                    "(id, cache_key, window_start, count) "
                    "VALUES (?, ?, ?, 1)",
                    (generate_id(), key, window_start),
                )
                count = 1
            except Exception as e:
                if not _is_unique_violation(e):
                    raise
                # Another worker inserted the row between our UPDATE and
                # INSERT; re-apply as an UPDATE so its increment is not lost.
                db.execute(update, params)
                row = db.fetch_one(
                    "SELECT count FROM dm_spam_counters "
                    "WHERE cache_key = ? AND window_start = ?",
                    params,
                )
                count = int(row["count"] if row else 1)

        # Sweep AFTER recording so the table is left within its cap (TTL-like
        # window expiry + hard bound) on every call, not one call late.
        AtomicWindowCounter._sweep_shared(db, now_s)
        return count <= int(limit)

    @staticmethod
    def _record_local(key: str, now_s: float, window_s: int, limit: int) -> bool:
        cutoff = now_s - window_s
        with _LOCAL_WINDOWS_LOCK:
            stamps = _LOCAL_WINDOWS.get(key)
            if stamps is None:
                if len(_LOCAL_WINDOWS) >= _LOCAL_WINDOW_MAX_KEYS:
                    # Insertion order approximates age; drop the oldest half.
                    for old in list(_LOCAL_WINDOWS)[: _LOCAL_WINDOW_MAX_KEYS // 2]:
                        del _LOCAL_WINDOWS[old]
                stamps = []
                _LOCAL_WINDOWS[key] = stamps
            while stamps and stamps[0] <= cutoff:
                stamps.pop(0)
            stamps.append(now_s)
            return len(stamps) <= limit


_COUNTER = AtomicWindowCounter()


class DMSpamDetector:
    """Detects and prevents spam in DM conversations."""

    # Default thresholds
    DEFAULT_RATE_LIMIT = 10  # messages per window
    DEFAULT_RATE_WINDOW = 60  # seconds
    DEFAULT_DUPLICATE_THRESHOLD = 3  # identical messages
    DEFAULT_DUPLICATE_WINDOW = 300  # seconds (5 minutes)
    DEFAULT_NEW_USER_RATE_LIMIT = 5  # stricter for new accounts
    DEFAULT_NEW_USER_AGE_MS = 7 * 24 * 3600 * 1000  # 7 days

    # Pattern heuristics
    URL_PATTERN = re.compile(r"\bhttps?://[^\s]+", re.IGNORECASE)
    MENTION_SPLIT_PATTERN = re.compile(r"<\s*@\s*")
    INVITE_MARKERS = ("discord.gg/", "discord.com/invite/", "t.me/")

    def __init__(self, db, auth_module=None):
        self._db = db
        self._auth = auth_module
        # The counter is process-wide (one detector graph per worker); bind the
        # shared DB so a Valkey outage still yields one global limit.
        _COUNTER.configure(db)

    def _get_timestamp(self) -> int:
        return timestamp_ms()

    def _generate_id(self) -> int:
        return generate_id()

    def _rate_key(self, sender_id: int, recipient_id: int) -> str:
        return f"dm_rate:{sender_id}:{recipient_id}"

    def _duplicate_key(
        self, sender_id: int, recipient_id: int, content_hash: str
    ) -> str:
        return f"dm_dup:{sender_id}:{recipient_id}:{content_hash}"

    def _is_new_user(self, sender_id: int) -> bool:
        """Check if the sender is a newly registered user."""
        try:
            row = self._db.fetch_one(
                "SELECT created_at FROM auth_users WHERE id = ?", (sender_id,)
            )
        except Exception as e:
            logger.debug(f"New-user lookup failed for {sender_id}: {e}")
            return False
        if not row:
            return False
        if isinstance(row, dict):
            created_at = row.get("created_at")
        elif hasattr(row, "keys"):
            try:
                created_at = dict(row).get("created_at")
            except Exception:
                created_at = None
        elif isinstance(row, (list, tuple)):
            created_at = row[0] if row else None
        else:
            created_at = getattr(row, "created_at", None)
        if created_at is None:
            return False
        age = self._get_timestamp() - created_at
        return age < self.DEFAULT_NEW_USER_AGE_MS

    def _get_user_filters(
        self, recipient_id: int, sender_id: int
    ) -> List[Dict[str, Any]]:
        """Get the recipient's enabled filters that apply to this sender.

        Deterministically ordered (created_at, id); global filters
        (``target_user_id IS NULL``) and per-sender targeted filters both apply.
        """
        try:
            rows = self._db.fetch_all(
                "SELECT * FROM dm_spam_filters WHERE user_id = ? AND enabled = 1 "
                "ORDER BY created_at ASC, id ASC",
                (recipient_id,),
            )
        except Exception as e:
            logger.debug(f"DM spam filter lookup failed for {recipient_id}: {e}")
            return []
        applicable = []
        for row in rows or []:
            filter_row = _row_dict(row)
            target = filter_row.get("target_user_id")
            if target is None or int(target) == int(sender_id):
                applicable.append(filter_row)
        return applicable

    @staticmethod
    def _filter_type_matches(flt: Dict[str, Any], filter_type: str) -> bool:
        """Compare filter types on canonical form (H6 variant hardening).

        A stored ``"Rate"``/``" rate "`` row is the same scope as ``"rate"``;
        strict ``==`` would silently ignore it and fall back to defaults
        (fail-open for blocks, fail-closed for warns).
        """
        return (
            str(flt.get("filter_type") or "").strip().casefold()
            == str(filter_type or "").strip().casefold()
        )

    @classmethod
    def _first_filter(
        cls, filters: List[Dict[str, Any]], filter_type: str
    ) -> Optional[Dict[str, Any]]:
        for flt in filters:
            if cls._filter_type_matches(flt, filter_type):
                return flt
        return None

    def _record_event(
        self,
        sender_id: int,
        recipient_id: int,
        event_type: str,
        content_hash: Optional[str] = None,
    ) -> None:
        """Record a DM spam event for tracking."""
        try:
            now = self._get_timestamp()
            event_id = self._generate_id()
            self._db.execute(
                """INSERT INTO dm_spam_events
                   (id, sender_id, recipient_id, event_type, content_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id, sender_id, recipient_id, event_type, content_hash, now),
            )
        except Exception as e:
            logger.warning(f"Failed to record DM spam event: {e}")

    def _enforce(
        self,
        action: str,
        event_type: str,
        sender_id: int,
        recipient_id: int,
        content_hash: Optional[str],
        reason: str,
    ) -> Tuple[bool, Optional[str]]:
        """Apply a filter's action to a detected violation.

        ``warn`` records the event but lets the message through; any other
        action (including the default ``block``) rejects the message.
        """
        effective_action = str(action or "block").strip().lower()
        stored_type = (
            event_type if effective_action != "warn" else f"{event_type}_warned"
        )
        self._record_event(sender_id, recipient_id, stored_type, content_hash)
        if effective_action == "warn":
            logger.info(f"DM spam warning for recipient {recipient_id}: {reason}")
            return True, None
        return False, reason

    def check_message(
        self,
        sender_id: int,
        recipient_id: int,
        content: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a DM message should be allowed or flagged as spam.

        Args:
            sender_id: ID of the message sender
            recipient_id: ID of the message recipient
            content: Message content

        Returns:
            Tuple of (allowed: bool, reason: Optional[str])
            If allowed is False, reason contains the spam detection reason.
        """
        try:
            return self._check_message(sender_id, recipient_id, content)
        except Exception as e:
            logger.warning(f"DM spam detection failed (failing open): {e}")
            return True, None

    def _check_message(
        self,
        sender_id: int,
        recipient_id: int,
        content: str,
    ) -> Tuple[bool, Optional[str]]:
        now_ms = self._get_timestamp()
        now_s = now_ms // 1000

        # H6: match/hash/count the canonical form so case, padding, and
        # Unicode variants of the same content cannot evade detection.
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = str(content)
        # Truncate before canonicalisation, hashing and pattern matching so a
        # hostile multi-megabyte message cannot make every check expensive.
        if len(content) > MAX_DM_CONTENT_LENGTH:
            content = content[:MAX_DM_CONTENT_LENGTH]
        canonical = _canonical_text(content)

        filters = self._get_user_filters(recipient_id, sender_id)
        rate_filter = self._first_filter(filters, "rate")
        duplicate_filter = self._first_filter(filters, "duplicate")

        # 1. Rate limit check. The attempt is recorded BEFORE the decision so
        # blocked messages still count inside the window (no free probing).
        rate_limit = self.DEFAULT_RATE_LIMIT
        rate_window = self.DEFAULT_RATE_WINDOW
        if rate_filter:
            rate_limit = _bounded_filter_int(
                rate_filter.get("threshold"),
                "rate threshold",
                1,
                MAX_FILTER_THRESHOLD,
                self.DEFAULT_RATE_LIMIT,
            )
            rate_window = _bounded_filter_int(
                rate_filter.get("window_seconds"),
                "rate window",
                1,
                MAX_FILTER_WINDOW_SECONDS,
                self.DEFAULT_RATE_WINDOW,
            )

        # New users get stricter limits
        if self._is_new_user(sender_id):
            rate_limit = min(rate_limit, self.DEFAULT_NEW_USER_RATE_LIMIT)

        rate_ok = _COUNTER.record_and_count(
            self._rate_key(sender_id, recipient_id), now_s, rate_window, rate_limit
        )
        if not rate_ok:
            return self._enforce(
                action=str(rate_filter.get("action")) if rate_filter else "block",
                event_type="rate_limited",
                sender_id=sender_id,
                recipient_id=recipient_id,
                content_hash=None,
                reason=f"Rate limit exceeded: {rate_limit} messages per {rate_window}s",
            )

        # 2. Duplicate content check, scoped per (sender, conversation) so the
        # same benign text sent to different recipients never accumulates.
        # The hash covers the canonical form: "Buy Crypto NOW " and
        # "buy crypto now" are the same message for spam purposes.
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        dup_threshold = self.DEFAULT_DUPLICATE_THRESHOLD
        dup_window = self.DEFAULT_DUPLICATE_WINDOW
        if duplicate_filter:
            dup_threshold = _bounded_filter_int(
                duplicate_filter.get("threshold"),
                "duplicate threshold",
                1,
                MAX_FILTER_THRESHOLD,
                self.DEFAULT_DUPLICATE_THRESHOLD,
            )
            dup_window = _bounded_filter_int(
                duplicate_filter.get("window_seconds"),
                "duplicate window",
                1,
                MAX_FILTER_WINDOW_SECONDS,
                self.DEFAULT_DUPLICATE_WINDOW,
            )

        dup_ok = _COUNTER.record_and_count(
            self._duplicate_key(sender_id, recipient_id, content_hash),
            now_s,
            dup_window,
            dup_threshold,
        )
        if not dup_ok:
            return self._enforce(
                action=str(duplicate_filter.get("action"))
                if duplicate_filter
                else "block",
                event_type="duplicate_spam",
                sender_id=sender_id,
                recipient_id=recipient_id,
                content_hash=content_hash,
                reason="Duplicate message detected",
            )

        # 3. Recipient-defined pattern filters (fnmatch-style globs), evaluated
        # in deterministic order; the first match decides. Both sides are
        # canonicalized so filter variants cannot be evaded by padding, case,
        # or Unicode lookalikes (H6).
        for flt in filters:
            if not self._filter_type_matches(flt, "pattern"):
                continue
            pattern = flt.get("pattern")
            if not pattern:
                continue
            canonical_pattern = _canonical_text(pattern)
            if not canonical_pattern:
                continue
            try:
                # Case-insensitive glob matching on canonical (casefolded)
                # strings: deterministic on every OS (fnmatch() alone defers
                # to os.path.normcase, which differs per platform). The
                # recipient-controlled pattern goes through the linear-time
                # _safe_fnmatchcase, never a raw backtracking `.*` chain.
                matched = _safe_fnmatchcase(canonical, canonical_pattern)
            except Exception as e:
                logger.debug(f"Invalid DM spam pattern skipped: {e}")
                continue
            if matched:
                return self._enforce(
                    action=str(flt.get("action", "block")),
                    event_type="pattern_matched",
                    sender_id=sender_id,
                    recipient_id=recipient_id,
                    content_hash=content_hash,
                    reason="Message matched your DM spam filter",
                )

        # 4. Built-in heuristic pattern check (URLs, mentions, invites)
        indicators = self._spam_indicators(content)
        if indicators >= 3:
            return self._enforce(
                action="block",
                event_type="pattern_spam",
                sender_id=sender_id,
                recipient_id=recipient_id,
                content_hash=content_hash,
                reason="Message flagged as potential spam",
            )

        return True, None

    @classmethod
    def _spam_indicators(cls, content: str) -> int:
        """Score heuristic spam indicators in the content.

        Block threshold is 3 (see _check_message step 4). Tiers are chosen so
        a single benign URL or a single shared invite (1 indicator) always
        passes, while bulk URL/mention bursts can block on their own (M11):
        3-5 URLs or 5-9 mentions alone stay under threshold, 6+ URLs or 10+
        mentions alone reach it, and invite markers count toward the URL
        bucket plus contribute their own unconditional signal.
        """
        # H6: count on the canonical form so fullwidth/zero-width/case
        # variants of URLs, mentions, and invite markers are still caught.
        text = _canonical_text(content)
        # Parse real URLs instead of counting raw substrings: case-insensitive
        # schemes are caught and embedded decoys like "xhttp://" are not.
        url_count = len(cls.URL_PATTERN.findall(text))
        # Collapse whitespace-split mention evasion ("< @123") before counting.
        mention_count = len(cls.MENTION_SPLIT_PATTERN.findall(text))
        # L4: invite markers count toward the URL bucket so invite-only spam
        # (e.g. three discord.gg links and nothing else) reaches the URL
        # threshold without needing http(s):// links at all.
        invite_count = sum(text.count(marker) for marker in cls.INVITE_MARKERS)
        url_count += invite_count

        indicators = 0
        if url_count >= 6:
            indicators += 3
        elif url_count >= 3:
            indicators += 2
        if mention_count >= 10:
            indicators += 3
        elif mention_count >= 5:
            indicators += 2
        # L4: previously the invite signal only counted when paired with 2+
        # URLs or mentions, so an isolated invite scored 0 and invite-only
        # spam evaded the heuristic entirely. Score it unconditionally; a
        # single shared invite (1 indicator) still cannot reach the block
        # threshold of 3, so legitimate invites keep passing.
        if invite_count:
            indicators += 1
        return indicators

    def get_spam_stats(self, user_id: int) -> Dict[str, Any]:
        """Get spam statistics for a user (as recipient)."""
        # Count events in last 24 hours
        day_ago = self._get_timestamp() - (24 * 3600 * 1000)
        rows = self._db.fetch_all(
            "SELECT event_type, COUNT(*) as count FROM dm_spam_events WHERE recipient_id = ? AND created_at > ? GROUP BY event_type",
            (user_id, day_ago),
        )
        stats = {
            (row["event_type"] if isinstance(row, dict) else row[0]): (
                row["count"] if isinstance(row, dict) else row[1]
            )
            for row in (rows or [])
        }

        # Get top spammers
        spammer_rows = self._db.fetch_all(
            "SELECT sender_id, COUNT(*) as count FROM dm_spam_events WHERE recipient_id = ? AND created_at > ? GROUP BY sender_id ORDER BY count DESC LIMIT 10",
            (user_id, day_ago),
        )
        top_spammers = [
            {
                "sender_id": row["sender_id"] if isinstance(row, dict) else row[0],
                "count": row["count"] if isinstance(row, dict) else row[1],
            }
            for row in (spammer_rows or [])
        ]

        return {
            "user_id": user_id,
            "period_hours": 24,
            "events_by_type": stats,
            "top_spammers": top_spammers,
        }

    def prune_old_events(self, retention_hours: int = 168) -> int:
        """Delete dm_spam_events older than the retention window.

        The events table only serves 24h-window statistics, so anything
        older than the retention horizon (default 7 days) is dead weight.
        Without this sweep every blocked attempt grew the table forever
        (unbounded on a busy instance). Bounded per call via LIMIT-style
        chunking is unnecessary here: the DELETE is fully indexed by the
        ``created_at`` column and runs on the maintenance cadence.

        Returns the number of rows removed.
        """
        if retention_hours <= 0:
            return 0
        cutoff = self._get_timestamp() - int(retention_hours * 3600 * 1000)
        try:
            cursor = self._db.execute(
                "DELETE FROM dm_spam_events WHERE created_at <= ?", (cutoff,)
            )
            return int(getattr(cursor, "rowcount", 0) or 0)
        except Exception as e:
            logger.warning(f"dm_spam_events retention prune failed: {e}")
            return 0

    def _ensure_filter_scope_uniqueness(self) -> None:
        """Install NULL-safe UNIQUE guards for filter scopes (best-effort).

        M12: the transactional UPDATE-then-INSERT upsert still has a TOCTOU
        window — two concurrent writers can both UPDATE 0 rows and both
        INSERT, creating duplicates. Partial UNIQUE indexes (one per scope,
        since ``= NULL`` never matches) turn the loser into a unique
        violation that set_filter retries as an UPDATE. The statements are
        idempotent (IF NOT EXISTS) and run on every upsert — deliberately not
        cached per-process, because tests and workers routinely open fresh
        database handles that each need the guard. Any failure (e.g.
        pre-existing duplicates, read-only handle) degrades to the previous
        transactional behavior.
        """
        try:
            self._db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_dm_spam_filters_global "
                "ON dm_spam_filters(user_id, filter_type) "
                "WHERE target_user_id IS NULL"
            )
            self._db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_dm_spam_filters_targeted "
                "ON dm_spam_filters(user_id, filter_type, target_user_id) "
                "WHERE target_user_id IS NOT NULL"
            )
        except Exception as e:
            logger.debug(f"DM spam filter uniqueness guard unavailable: {e}")

    def _update_filter_row(
        self,
        user_id: int,
        filter_type: str,
        target_user_id: Optional[int],
        action: str,
        threshold: int,
        window_seconds: int,
        pattern: Optional[str],
        now: int,
    ) -> int:
        cursor = self._db.execute(
            """UPDATE dm_spam_filters SET action = ?, threshold = ?,
               window_seconds = ?, pattern = ?, enabled = 1, updated_at = ?
               WHERE user_id = ? AND filter_type = ?
                 AND ((target_user_id IS NULL AND ? IS NULL)
                      OR target_user_id = ?)""",
            (
                action,
                threshold,
                window_seconds,
                pattern,
                now,
                user_id,
                filter_type,
                target_user_id,
                target_user_id,
            ),
        )
        return int(getattr(cursor, "rowcount", 0) or 0)

    def set_filter(
        self,
        user_id: int,
        filter_type: str = "rate",
        action: str = "warn",
        threshold: int = 10,
        window_seconds: int = 60,
        target_user_id: Optional[int] = None,
        pattern: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set or update DM spam filter for a user.

        Transactional upsert: global (NULL-target) and per-sender targeted
        filters are matched with proper IS NULL semantics so an update can
        never duplicate rows or rewrite the wrong scope. The scope key is
        canonicalized (H6) so ``"Rate"``/``" rate "`` variants cannot fork
        duplicate rows, and a lost concurrent-insert race is retried as an
        UPDATE via the NULL-safe UNIQUE guards (M12).
        """
        # H6: canonicalize the scope key before matching.
        filter_type = str(filter_type or "rate").strip().casefold() or "rate"
        action = str(action or "warn").strip().casefold() or "warn"
        if filter_type not in DM_FILTER_TYPES:
            raise ValueError(f"invalid DM spam filter type: {filter_type!r}")
        if action not in DM_FILTER_ACTIONS:
            raise ValueError(f"invalid DM spam filter action: {action!r}")
        # These are caller-supplied; reject values outside the bounds instead
        # of silently storing a burst limit that can never trip or one that
        # grows the local window list forever.
        threshold = bounded_int(
            threshold, "threshold", minimum=1, maximum=MAX_FILTER_THRESHOLD
        )
        window_seconds = bounded_int(
            window_seconds,
            "window_seconds",
            minimum=1,
            maximum=MAX_FILTER_WINDOW_SECONDS,
        )
        if target_user_id is not None:
            target_user_id = int(target_user_id)
        if pattern is not None:
            pattern = str(pattern)
            if len(pattern) > MAX_FILTER_PATTERN_LENGTH:
                raise ValueError(
                    f"pattern exceeds {MAX_FILTER_PATTERN_LENGTH} characters"
                )
        now = self._get_timestamp()

        self._ensure_filter_scope_uniqueness()
        self._db.begin_transaction()
        try:
            updated = self._update_filter_row(
                user_id,
                filter_type,
                target_user_id,
                action,
                threshold,
                window_seconds,
                pattern,
                now,
            )
            if updated == 0:
                try:
                    filter_id = self._generate_id()
                    self._db.execute(
                        """INSERT INTO dm_spam_filters
                           (id, user_id, target_user_id, pattern, filter_type, action,
                            threshold, window_seconds, enabled, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                        (
                            filter_id,
                            user_id,
                            target_user_id,
                            pattern,
                            filter_type,
                            action,
                            threshold,
                            window_seconds,
                            now,
                            now,
                        ),
                    )
                except Exception as e:
                    if not _is_unique_violation(e):
                        raise
                    # M12: lost the concurrent-insert race — the row now
                    # exists, so re-apply as an UPDATE instead of duplicating.
                    self._update_filter_row(
                        user_id,
                        filter_type,
                        target_user_id,
                        action,
                        threshold,
                        window_seconds,
                        pattern,
                        now,
                    )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        logger.debug(f"User {user_id} set DM spam filter: type={filter_type}")
        return {"user_id": user_id, "filter_type": filter_type, "action": action}

    def remove_filter(self, user_id: int, filter_id: int) -> bool:
        """Remove a DM spam filter. Returns False when nothing was deleted."""
        cursor = self._db.execute(
            "DELETE FROM dm_spam_filters WHERE id = ? AND user_id = ?",
            (filter_id, user_id),
        )
        return getattr(cursor, "rowcount", 0) > 0

    def get_filters(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all DM spam filters for a user."""
        rows = self._db.fetch_all(
            "SELECT * FROM dm_spam_filters WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        )
        return [_row_dict(row) for row in (rows or [])]
