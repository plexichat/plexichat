"""
Session management - Manages gateway sessions and connections.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Set, Any
import math
import time
import secrets
import threading
from src.core.database import get_valkey_client, valkey_available
import utils.logger as logger

from .connection import Connection, ConnectionState
from .boundaries import (
    MAX_REPLAY_EVENT_BYTES,
    MAX_SESSION_REPLAY_BYTES,
    bounded_batch,
    payload_size,
)
from .intents import PRIVILEGED_INTENTS, filter_event_by_intents, validate_intents


#: Private key under which a recorded replay frame carries the authoritative
#: ``(server_id, channel_id)`` scope it was dispatched for. It lives on the
#: frame's top level (never inside ``d``), is stripped before any frame is sent
#: on the wire, and is only ever written by ``SessionManager.record_event``.
REPLAY_SCOPE_KEY = "__gateway_authz_scope"


def _replay_scope_id(value: Any) -> Optional[int]:
    """Coerce a recorded scope id to int (bools/unparseable values -> None)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_resume_intents(raw: Any) -> int:
    """Return the intent mask a resumed connection is allowed to use.

    IDENTIFY creates sessions only with a validated, non-privileged intent
    mask.  A gateway session, however, deliberately outlives its socket and
    may be hydrated from another worker's persisted (Valkey) payload, so the
    mask cached on it is untrusted at resume time: it can be stale (created
    before privileged intents required approval) or corrupted.  Restoring it
    verbatim would let a resumed session regain privileged event visibility
    (GUILD_MEMBERS / GUILD_PRESENCES / MESSAGE_CONTENT) that it could no
    longer obtain at IDENTIFY -- a privilege re-escalation via RESUME.

    Fail closed: an invalid mask grants nothing, and privileged bits are
    always stripped.  This only ever *reduces* the mask, so no existing
    check is weakened.
    """
    if not isinstance(raw, int) or isinstance(raw, bool) or not validate_intents(raw):
        return 0
    return raw & ~PRIVILEGED_INTENTS


def _replay_event_allowed(payload: Any, intents: int) -> bool:
    """Re-apply the intent gate to a frame restored from a replay buffer.

    A gateway session outlives its socket and may be hydrated from a persisted
    payload recorded under a wider (privileged) intent mask. RESUME strips
    privileged bits before handing the mask to the new connection, but the
    replay buffer is restored verbatim: without a second gate here replay
    re-escalates event visibility that IDENTIFY/RESUME deliberately deny.
    Known event types are re-checked against the *current* mask and fail
    closed; unrecognised ``t`` values carry no intent requirement at dispatch
    time either, so they keep the existing behaviour.
    """
    from src.core.events.models import Event
    from src.core.events.types import EventType

    if not isinstance(payload, dict):
        return False
    name = payload.get("t")
    if not isinstance(name, str):
        return True
    try:
        event_type = EventType(name)
    except ValueError:
        return True
    data = payload.get("d")
    if not isinstance(data, dict):
        data = {}

    def _as_id(value: Any) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None

    server_id = _as_id(data.get("server_id"))
    if server_id is None:
        server_id = _as_id(data.get("guild_id"))
    channel_id = _as_id(data.get("channel_id"))
    event = Event(
        event_type=event_type,
        data=data,
        server_id=server_id,
        channel_id=channel_id,
    )
    return filter_event_by_intents(event, intents)


# Sentinel for a value whose serialized size cannot be proven from a cheap
# structural key (anything ``json.dumps`` would route through ``default=str``).
# Such events are always re-measured with ``payload_size`` so the cache can
# never weaken the byte guard.
_UNCACHEABLE = object()

_JSON_SCALARS = (bool, int, float, str)


def _size_key(value: Any) -> Any:
    """Cheap stand-in whose equality mirrors ``payload_size`` for JSON data.

    Two values with equal ``_size_key`` serialize to the same JSON bytes under
    ``payload_size`` (same keys, same string contents, same container shape),
    so a cached size stays valid as long as the key matches. Strings are held
    by reference, not copied: comparing an unchanged key is O(1) per string
    thanks to CPython's identity shortcut. Non-JSON values (custom objects,
    tuples, non-string dict keys, ...) yield ``_UNCACHEABLE`` and force a real
    measurement, so no unusual payload can slip past the cap on a stale key.
    """
    if value is None or type(value) in _JSON_SCALARS:
        return value
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if not isinstance(key, str):
                return _UNCACHEABLE
            sub = _size_key(item)
            if sub is _UNCACHEABLE:
                return _UNCACHEABLE
            parts.append((key, sub))
        return ("dict", tuple(parts))
    if isinstance(value, list):
        parts = []
        for item in value:
            sub = _size_key(item)
            if sub is _UNCACHEABLE:
                return _UNCACHEABLE
            parts.append(sub)
        return ("list", tuple(parts))
    return _UNCACHEABLE


#: Hard ceiling on sessions retained in this worker's local map. Sessions
#: must outlive their socket so a client can RESUME, and ``remove_connection``
#: deliberately does not drop them. ``cleanup_stale_sessions`` exists but is
#: only ever called from tests (no production janitor), so without a cap a
#: single ticket-holding client can grow ``_sessions`` and its replay buffers
#: without bound by churning IDENTIFY on short-lived sockets. Over the cap we
#: evict the least-recently-active session; denying an occasional RESUME is the
#: safe direction versus unbounded memory growth.
MAX_LOCAL_SESSIONS = 8192

#: Absolute ceiling on a resumable gateway session's lifetime, measured from
#: its persisted ``created_at_epoch``. The inactivity timeout
#: (``session_timeout_ms``) and the Valkey TTL are both refreshed on every
#: resume/dispatch, so on their own they let a session created under an older
#: authorization state (before a demotion, ban or guild/artifact/SUM
#: membership change) be revived indefinitely. This cap bounds that window:
#: a session older than the ceiling is refused at RESUME and dropped from the
#: shared store, so a stale authorization context cannot be replayed forever.
MAX_SESSION_LIFETIME_MS = 7 * 24 * 60 * 60 * 1000  # 7 days

#: Small tolerance for wall-clock skew between workers: a persisted creation
#: time slightly in the future is fine, a wildly future-dated one fails closed.
MAX_SESSION_CREATION_SKEW_MS = 60 * 1000


@dataclass
class Session:
    """Represents a resumable gateway session."""

    session_id: str
    user_id: int
    sequence: int = 0
    intents: int = 0
    created_at: float = field(default_factory=time.monotonic)
    created_at_epoch: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.monotonic)
    # Wall-clock timestamp is persisted so a session can be resumed by a
    # different worker. ``last_activity`` remains monotonic for local checks.
    last_activity_epoch: float = field(default_factory=time.time)
    connection_id: Optional[str] = None
    replay_events: List[Dict[str, Any]] = field(default_factory=list)
    max_replay_events: int = 100
    # Running serialized size of ``replay_events``, maintained by
    # ``add_replay_event`` and rebuilt in ``__post_init__`` so foreign state
    # (cross-worker hydration) cannot smuggle an oversized buffer past the
    # total-byte ceiling.
    replay_bytes: int = field(default=0, repr=False)
    # Per-event size cache, aligned index-for-index with ``replay_events``.
    # Each entry is ``(signature, size)`` where ``signature`` is a cheap
    # structural key; ``_UNCACHEABLE`` signatures re-measure on use. The cache
    # is validated against the live buffer before every read, so a caller that
    # mutates ``replay_events`` directly falls back to the real ``payload_size``
    # guard instead of trusting a stale size. Kept per instance (never shared)
    # so one session's measurements can never leak into another's resume.
    _replay_size_cache: List[tuple] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        # A corrupted/persisted limit must not turn the replay buffer into an
        # unbounded list (or a zero-length one that silently drops history).
        try:
            self.max_replay_events = bounded_batch(self.max_replay_events, default=100)
        except ValueError:
            self.max_replay_events = 100
        # Events supplied at construction never passed through
        # ``add_replay_event``. This is the cross-worker hydration path in
        # particular: apply the same guards before any of them can be replayed
        # to a resuming client. ``sanitize_replay_events`` returns a fresh list
        # so a foreign/mutable payload cannot alias the session's buffer.
        if self.replay_events:
            kept, sizes = self._sanitize_with_sizes(
                self.replay_events, self.max_replay_events
            )
            self.replay_events = kept
            # Build the cache from the sizes sanitize already measured: no
            # second serialization of the whole buffer (the old code did one
            # pass here after sanitize's pass).
            self._replay_size_cache = [
                (_size_key(event), size) for event, size in zip(kept, sizes)
            ]
            self.replay_bytes = sum(sizes)
        else:
            self._replay_size_cache = []
            self.replay_bytes = 0

    @staticmethod
    def _sanitize_with_sizes(
        events: object, max_events: int
    ) -> tuple[List[Dict[str, Any]], List[int]]:
        """Bounded sanitize that also returns each kept event's measured size.

        Keeping the measurement lets hydration seed the size cache in a single
        pass instead of serializing the buffer twice.
        """
        kept: List[Dict[str, Any]] = []
        sizes: List[int] = []
        total = 0
        limit = max(1, int(max_events))
        if not isinstance(events, (list, tuple)):
            return kept, sizes
        for event in events:
            if not isinstance(event, dict):
                continue
            size = payload_size(event)
            if size > MAX_REPLAY_EVENT_BYTES:
                logger.warning(
                    f"Dropping oversized replay event during session hydration "
                    f"(>{MAX_REPLAY_EVENT_BYTES} bytes)"
                )
                continue
            while kept and (
                len(kept) >= limit or total + size > MAX_SESSION_REPLAY_BYTES
            ):
                kept.pop(0)
                total -= sizes.pop(0)
            if total + size > MAX_SESSION_REPLAY_BYTES:
                continue
            kept.append(event)
            sizes.append(size)
            total += size
        return kept, sizes

    @staticmethod
    def sanitize_replay_events(events: object, max_events: int) -> List[Dict[str, Any]]:
        """Return a bounded, well-formed replay list from untrusted ``events``.

        Drops non-dict entries and per-event oversized frames, and evicts the
        oldest entries until the list fits both ``max_events`` and the total
        ``MAX_SESSION_REPLAY_BYTES`` budget. Fail closed: anything that cannot
        be measured is dropped rather than replayed.
        """
        kept, _ = Session._sanitize_with_sizes(events, max_events)
        return kept

    def _replay_sizes_current(self) -> Optional[List[Optional[int]]]:
        """Validate the size cache against the live buffer.

        Returns a per-index list of cached sizes (``None`` for entries that
        must be re-measured), or ``None`` if the buffer was changed externally
        and no cached size may be trusted. External mutation is detected by a
        length mismatch or by a changed structural signature (whose string
        contents point at the same objects, so validation stays cheap).
        """
        events = self.replay_events
        cache = self._replay_size_cache
        if len(cache) != len(events):
            return None
        sizes: List[Optional[int]] = []
        for event, (signature, size) in zip(events, cache):
            if not isinstance(event, dict):
                return None
            if signature is _UNCACHEABLE:
                sizes.append(None)
                continue
            if _size_key(event) != signature:
                return None
            sizes.append(size)
        return sizes

    def add_replay_event(self, event: Dict[str, Any]) -> None:
        """Add an event to the replay buffer.

        The buffer is bounded by count (``max_replay_events``) and by the
        total ``MAX_SESSION_REPLAY_BYTES`` budget: once either is exceeded the
        oldest entry is evicted, so a long disconnect can never miss more than
        the window and a single session cannot amplify into an unbounded replay
        frame. Eviction is logged at WARNING so replay gaps are observable.
        """
        # A caller may have mutated ``replay_events`` directly since the last
        # write. Rebuild only when the cache no longer aligns (length changed);
        # content-level changes are re-validated on every resume read below.
        if len(self._replay_size_cache) != len(self.replay_events):
            self._rebuild_replay_size_cache()
        # Never queue an oversized frame for later replay: a single huge
        # event would be re-sent to every resuming connection. Use the shared
        # size helper so the guard matches hydration and every other boundary.
        size = (
            payload_size(event)
            if isinstance(event, dict)
            else MAX_REPLAY_EVENT_BYTES + 1
        )
        if size > MAX_REPLAY_EVENT_BYTES:
            logger.warning(
                f"Session {self.session_id}: dropping oversized replay event "
                f"(>{MAX_REPLAY_EVENT_BYTES} bytes)"
            )
            return
        self.replay_events.append(event)
        self._replay_size_cache.append((_size_key(event), size))
        self.replay_bytes += size
        evicted = False
        while self.replay_events and (
            len(self.replay_events) > self.max_replay_events
            or self.replay_bytes > MAX_SESSION_REPLAY_BYTES
        ):
            self.replay_events.pop(0)
            _, dropped_size = self._replay_size_cache.pop(0)
            self.replay_bytes -= dropped_size
            evicted = True
        if evicted and not getattr(self, "_replay_overflow_warned", False):
            self._replay_overflow_warned = True
            logger.warning(
                f"Session {self.session_id}: replay buffer overflow "
                f"(max {self.max_replay_events} events / "
                f"{MAX_SESSION_REPLAY_BYTES} bytes); oldest events evicted "
                f"and unrecoverable on resume"
            )

    def _rebuild_replay_size_cache(self) -> None:
        """Recompute the per-event cache from the current buffer in one pass."""
        cache: List[tuple] = []
        for event in self.replay_events:
            key = _size_key(event) if isinstance(event, dict) else _UNCACHEABLE
            cache.append((key, payload_size(event)))
        self._replay_size_cache = cache

    def get_replay_events(self, after_sequence: int) -> List[Dict[str, Any]]:
        """Get events after a specific sequence number.

        Final backstop: re-check the per-event ceiling here so even a caller
        that mutates ``replay_events`` directly cannot leak an oversized frame
        to a resuming client. Sizes are taken from a validated per-session
        cache; any external change to the buffer invalidates it and forces a
        full re-measure, so the guard is never weaker than ``payload_size``.
        """
        sizes = self._replay_sizes_current()
        result: List[Dict[str, Any]] = []
        for index, event in enumerate(self.replay_events):
            if not isinstance(event, dict) or event.get("s", 0) <= after_sequence:
                continue
            size = sizes[index] if sizes is not None else None
            if size is None:
                size = payload_size(event)
            if size <= MAX_REPLAY_EVENT_BYTES:
                result.append(event)
        return result

    def prune_replay_events_for_intents(self, intents: int) -> None:
        """Drop buffered frames the resumed connection's mask no longer allows.

        A stale/corrupt persisted session may carry frames recorded under
        privileged intents. Once those intents are stripped at RESUME the
        frames must not be replayed, so heal the cached buffer here (the same
        way ``session.intents`` is healed) and keep the size cache and byte
        total aligned with the pruned list.
        """
        cache = self._replay_size_cache
        if len(cache) != len(self.replay_events):
            self._rebuild_replay_size_cache()
            cache = self._replay_size_cache
        kept_events = []
        kept_cache = []
        for event, entry in zip(self.replay_events, cache):
            if _replay_event_allowed(event, intents):
                kept_events.append(event)
                kept_cache.append(entry)
        self.replay_events = kept_events
        self._replay_size_cache = kept_cache
        self.replay_bytes = sum(size for _sig, size in kept_cache)

    def update_activity(self) -> None:
        """Update local and cross-worker activity timestamps."""
        self.last_activity = time.monotonic()
        self.last_activity_epoch = time.time()


class SessionManager:
    """Manages all gateway sessions and connections."""

    def __init__(
        self,
        heartbeat_interval_ms: int = 45000,
        session_timeout_ms: int = 60000,
        max_connections_per_user: int = 5,
        max_local_sessions: int = MAX_LOCAL_SESSIONS,
        max_session_lifetime_ms: int = MAX_SESSION_LIFETIME_MS,
    ):
        """
        Initialize the session manager.

        Args:
            heartbeat_interval_ms: Heartbeat interval in milliseconds
            session_timeout_ms: Session timeout for resume in milliseconds
            max_connections_per_user: Maximum concurrent connections per user
            max_local_sessions: Hard cap on locally retained resumable sessions
            max_session_lifetime_ms: Absolute ceiling on a resumable session's
                age, measured from its persisted ``created_at_epoch``
        """
        self._heartbeat_interval_ms = heartbeat_interval_ms
        self._session_timeout_ms = session_timeout_ms
        self._max_connections_per_user = max_connections_per_user
        # An operator may shorten the lifetime but never disable or extend it
        # past the hard ceiling. Corrupt config fails closed to the default.
        try:
            self._max_session_lifetime_ms = max(
                1, min(int(max_session_lifetime_ms), MAX_SESSION_LIFETIME_MS)
            )
        except (TypeError, ValueError, OverflowError):
            self._max_session_lifetime_ms = MAX_SESSION_LIFETIME_MS
        # Corrupted/negative config must not disable the cap.
        try:
            self._max_local_sessions = max(
                1, min(int(max_local_sessions), MAX_LOCAL_SESSIONS)
            )
        except (TypeError, ValueError, OverflowError):
            self._max_local_sessions = MAX_LOCAL_SESSIONS

        self._connections: Dict[str, Connection] = {}
        self._sessions: Dict[str, Session] = {}
        self._user_connections: Dict[int, Set[str]] = {}
        self._lock = threading.Lock()

        # Valkey settings
        self._worker_id = "unknown"
        client = get_valkey_client()
        if client:
            self._worker_id = client.worker_id

    def _session_key(self, session_id: str) -> str:
        return f"gateway:session:{session_id}"

    def _session_to_payload(self, session: Session) -> Dict[str, Any]:
        return {
            "session_id": session.session_id,
            "user_id": session.user_id,
            "sequence": session.sequence,
            "intents": session.intents,
            "created_at_epoch": session.created_at_epoch,
            "last_activity_epoch": session.last_activity_epoch,
            "replay_events": session.replay_events[-session.max_replay_events :],
        }

    def _persist_session(self, session: Session) -> None:
        """Persist resumable identity and replay state for sibling workers."""
        if not valkey_available():
            return
        client = get_valkey_client()
        if not client or not hasattr(client, "set_json"):
            return
        try:
            client.set_json(
                self._session_key(session.session_id),
                self._session_to_payload(session),
                ttl=max(1, int(self._session_timeout_ms / 1000)),
            )
        except Exception as exc:
            logger.debug(f"Valkey session persistence failed: {exc}")

    def _delete_shared_session(self, session_id: str) -> None:
        """Drop a session's cross-worker record (best effort, no lock held)."""
        if not valkey_available():
            return
        client = get_valkey_client()
        if not client:
            return
        try:
            client.delete(self._session_key(session_id))
        except Exception as exc:
            logger.debug(f"Valkey session removal failed: {exc}")

    def _evict_sessions_if_needed(self) -> None:
        """Keep the local session map under ``_max_local_sessions``.

        Called after every insertion (new IDENTIFY or cross-worker hydration).
        First drop sessions that are past the resume timeout *and* have no
        live local connection — those can never be resumed and are pure leak.
        If that is not enough, evict the least-recently-active sessions. The
        shared record is deleted alongside each eviction so a sibling worker
        (or a later lookup here) cannot immediately rehydrate the same state
        and defeat the cap. Valkey I/O is done outside the lock.
        """
        with self._lock:
            if len(self._sessions) <= self._max_local_sessions:
                return
            now_mono = time.monotonic()
            now_epoch = time.time()
            timeout = self._session_timeout_ms / 1000
            stale = [
                sid
                for sid, sess in self._sessions.items()
                if sess.connection_id not in self._connections
                and (
                    now_mono - sess.last_activity > timeout
                    or (
                        sess.last_activity_epoch
                        and now_epoch - sess.last_activity_epoch > timeout
                    )
                )
            ]
            evicted = []
            for sid in stale:
                if len(self._sessions) <= self._max_local_sessions:
                    break
                self._sessions.pop(sid, None)
                evicted.append(sid)
            # Still over the cap: shed the coldest sessions regardless of
            # resumability. Bounded memory wins over an unlikely late RESUME.
            while len(self._sessions) > self._max_local_sessions:
                victim = min(
                    self._sessions,
                    key=lambda sid: self._sessions[sid].last_activity,
                )
                self._sessions.pop(victim, None)
                evicted.append(victim)
        for sid in evicted:
            self._delete_shared_session(sid)
        if evicted:
            logger.warning(
                f"Session map at cap ({self._max_local_sessions}); evicted "
                f"{len(evicted)} stale/idle sessions"
            )

    def _session_creation_valid(
        self, created_at_epoch: object, *, now: Optional[float] = None
    ) -> bool:
        """Revalidate a persisted session creation time before allowing RESUME.

        ``created_at_epoch`` is persisted verbatim and restored from Valkey, so
        it is untrusted: it may be missing, corrupt, absurdly future-dated, or
        simply older than the maximum session lifetime. The inactivity check
        on ``last_activity_epoch`` cannot catch the last case because activity
        is refreshed on every resume/dispatch. Fail closed on all of them so a
        session minted under a stale authorization state cannot be revived.
        """
        if isinstance(created_at_epoch, bool) or not isinstance(
            created_at_epoch, (int, float)
        ):
            return False
        value = float(created_at_epoch)
        if not math.isfinite(value):
            return False
        current = time.time() if now is None else now
        age_ms = (current - value) * 1000.0
        if age_ms > self._max_session_lifetime_ms:
            return False
        if age_ms < -MAX_SESSION_CREATION_SKEW_MS:
            return False
        return True

    def _load_shared_session(self, session_id: str) -> Optional[Session]:
        """Hydrate a session created by another worker, if it is still valid."""
        if not valkey_available():
            return None
        client = get_valkey_client()
        if not client or not hasattr(client, "get_json"):
            return None
        try:
            raw = client.get_json(self._session_key(session_id))
            if not isinstance(raw, dict):
                return None
            # The payload must belong to the key it was read from. Otherwise a
            # stale/mis-keyed record could be resumed under a different id.
            stored_id = raw.get("session_id")
            if not isinstance(stored_id, str) or stored_id != session_id:
                return None
            user_id = int(raw["user_id"])
            if user_id <= 0:
                return None
            # Fail closed on an invalid intent mask instead of applying it to
            # the resumed connection (which would silently blackhole events).
            raw_intents = raw.get("intents", 0)
            intents = int(raw_intents) if validate_intents(raw_intents) else 0
            # A persisted creation time is untrusted. Reject sessions with no
            # valid creation timestamp or ones past the absolute lifetime,
            # even when ``last_activity_epoch`` is still fresh (it is refreshed
            # on every resume/dispatch and cannot bound a session's age).
            created_epoch = raw.get("created_at_epoch", 0)
            if not self._session_creation_valid(created_epoch):
                logger.debug(
                    f"Rejecting shared gateway session {session_id}: "
                    f"invalid or expired creation time"
                )
                return None
            last_epoch = float(raw.get("last_activity_epoch", 0))
            if time.time() - last_epoch > self._session_timeout_ms / 1000:
                return None
            # ``Session.__post_init__`` enforces the count, per-event byte and
            # total-byte limits on ``replay_events`` here: this is the only
            # path where events were not written through ``add_replay_event``.
            session = Session(
                session_id=stored_id,
                user_id=user_id,
                sequence=int(raw.get("sequence", 0)),
                intents=intents,
                created_at_epoch=float(created_epoch),
                last_activity_epoch=last_epoch,
                replay_events=raw.get("replay_events") or [],
            )
            # Hydration must not refresh activity: merely probing a session
            # must not extend its resume TTL. Activity is refreshed only after
            # a successful resume or event dispatch.
            session.last_activity = time.monotonic()
            return session
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            logger.debug(f"Invalid shared gateway session {session_id}: {exc}")
            return None
        except Exception as exc:
            logger.debug(f"Valkey session hydration failed: {exc}")
            return None

    def _valkey_register_connection(self, user_id: int, connection_id: str) -> None:
        """Register a connection in Valkey for global tracking."""
        if not valkey_available():
            return

        client = get_valkey_client()
        if not client:
            return

        try:
            # 1. Increment global connection count
            client.incr("stats:total_connections")

            # 2. Add to user's global connection set
            client.sadd(
                f"user:{user_id}:connections", f"{self._worker_id}:{connection_id}"
            )
            client.expire(f"user:{user_id}:connections", 86400)  # 24h safety TTL

            # 3. Register worker location for this connection
            client.hset(f"conn:{connection_id}:info", "worker", self._worker_id)
            client.hset(f"conn:{connection_id}:info", "user_id", str(user_id))
            client.expire(f"conn:{connection_id}:info", 3600)  # 1h safety TTL
        except Exception as e:
            logger.debug(f"Valkey session registration failed: {e}")

    def _valkey_unregister_connection(self, user_id: int, connection_id: str) -> None:
        """Unregister a connection from Valkey."""
        if not valkey_available():
            return

        client = get_valkey_client()
        if not client:
            return

        try:
            client.decr("stats:total_connections")
            client.srem(
                f"user:{user_id}:connections", f"{self._worker_id}:{connection_id}"
            )
            client.delete(f"conn:{connection_id}:info")
        except Exception as e:
            logger.debug(f"Valkey session unregistration failed: {e}")

    @property
    def worker_id(self) -> str:
        """The unique identity of this worker instance."""
        return self._worker_id

    @property
    def heartbeat_interval_ms(self) -> int:
        """Get the heartbeat interval."""
        return self._heartbeat_interval_ms

    def generate_connection_id(self) -> str:
        """Generate a unique connection ID."""
        return secrets.token_hex(16)

    def generate_session_id(self) -> str:
        """Generate a unique session ID."""
        return secrets.token_hex(16)

    def add_connection(self, connection: Connection) -> None:
        """
        Add a new connection.

        Args:
            connection: Connection to add
        """
        user_id = connection.user_id
        conn_id = connection.connection_id
        with self._lock:
            self._connections[conn_id] = connection
        # Valkey I/O outside lock to avoid blocking all connection operations
        if user_id:
            self._valkey_register_connection(user_id, conn_id)

    def remove_connection(self, connection_id: str) -> Optional[Connection]:
        """
        Remove a connection.

        Args:
            connection_id: Connection ID to remove

        Returns:
            Removed connection or None
        """
        connection = None
        user_id = None
        with self._lock:
            connection = self._connections.pop(connection_id, None)
            if connection and connection.user_id:
                user_id = connection.user_id
                user_conns = self._user_connections.get(user_id, set())
                user_conns.discard(connection_id)
                if not user_conns:
                    self._user_connections.pop(user_id, None)
        # Valkey I/O outside lock
        if user_id and connection:
            self._valkey_unregister_connection(user_id, connection_id)
        return connection

    def get_connection(self, connection_id: str) -> Optional[Connection]:
        """Get a connection by ID."""
        return self._connections.get(connection_id)

    def get_user_connections(self, user_id: int) -> List[Connection]:
        """Get all connections for a user."""
        with self._lock:
            conn_ids = self._user_connections.get(user_id, set())
            return [
                self._connections[cid] for cid in conn_ids if cid in self._connections
            ]

    def get_user_connection_count(self, user_id: int) -> int:
        """Get the number of connections for a user (local and global)."""
        # 1. Start with local count
        with self._lock:
            local_count = len(self._user_connections.get(user_id, set()))

        # 2. Add global count if available
        if valkey_available():
            client = get_valkey_client()
            if client:
                try:
                    # smembers returns a set of strings like "worker_id:connection_id"
                    global_conns = client.smembers(f"user:{user_id}:connections")
                    return len(global_conns)
                except Exception as count_error:
                    # Fall back to the in-memory connection count below.
                    logger.debug(f"global connection count failed: {count_error}")

        return local_count

    def can_user_connect(self, user_id: int) -> bool:
        """Check if user can create a new connection (globally)."""
        return self.get_user_connection_count(user_id) < self._max_connections_per_user

    def create_session(
        self,
        connection: Connection,
        user_id: int,
        intents: int,
    ) -> Session:
        """
        Create a new session for a connection.

        Args:
            connection: Connection to create session for
            user_id: User ID
            intents: Gateway intents

        Returns:
            Created session
        """
        session_id = self.generate_session_id()
        session = Session(
            session_id=session_id,
            user_id=user_id,
            intents=intents,
            connection_id=connection.connection_id,
        )

        with self._lock:
            self._sessions[session_id] = session
            user_conns = self._user_connections.setdefault(user_id, set())
            user_conns.add(connection.connection_id)

        # Register in Valkey since we now know the user_id
        self._valkey_register_connection(user_id, connection.connection_id)
        self._persist_session(session)
        # Sessions outlive their socket for RESUME; nothing removes them in
        # production, so enforce the hard cap on every insertion.
        self._evict_sessions_if_needed()

        connection.set_identified(user_id, session_id, intents)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a local or cross-worker session by ID."""
        session = self._sessions.get(session_id)
        if session is not None:
            return session
        shared = self._load_shared_session(session_id)
        if shared is not None:
            with self._lock:
                self._sessions[session_id] = shared
            # Hydration inserts into the same map; cap it here too so an
            # attacker cannot grow the map by probing many valid ids.
            self._evict_sessions_if_needed()
        return shared

    def remove_session(self, session_id: str) -> Optional[Session]:
        """Remove a local and shared session."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        self._delete_shared_session(session_id)
        return session

    def can_resume_session(self, session_id: str, user_id: int) -> bool:
        """
        Check if a session can be resumed.

        Args:
            session_id: Session ID to check
            user_id: User ID attempting resume

        Returns:
            True if session can be resumed
        """
        session = self.get_session(session_id)
        if not session:
            return False

        if session.user_id != user_id:
            return False

        # The inactivity timeout alone is insufficient: activity is refreshed
        # on every resume/dispatch, so a session created long ago (possibly
        # before a privilege/ban/membership change) would otherwise stay
        # resumable forever. Revalidate the persisted creation time too.
        if not self._session_creation_valid(session.created_at_epoch):
            self.remove_session(session_id)
            return False

        timeout_seconds = self._session_timeout_ms / 1000
        if time.time() - session.last_activity_epoch > timeout_seconds:
            self.remove_session(session_id)
            return False

        return True

    def resume_session(
        self,
        connection: Connection,
        session_id: str,
        sequence: int,
    ) -> Optional[Session]:
        """
        Resume an existing session.

        Args:
            connection: New connection
            session_id: Session ID to resume
            sequence: Last sequence number received

        Returns:
            Resumed session or None
        """
        session = self.get_session(session_id)
        if not session:
            return None

        # Defense in depth: direct callers may skip ``can_resume_session``.
        # Never resume a session whose persisted creation time is past the
        # absolute lifetime, even if its activity timestamp is still fresh.
        if not self._session_creation_valid(session.created_at_epoch):
            self.remove_session(session_id)
            return None

        # Enforce the same per-user connection cap IDENTIFY enforces. A resume
        # whose previous socket is still tracked locally only MOVES the slot:
        # resume_session() disconnects that old socket and discards it from the
        # user's set below, so the count does not grow and the resume is safe.
        # A session whose old socket is already gone, however, allocates a
        # brand-new counted connection. Without this check a client could churn
        # IDENTIFY/disconnect to accumulate resumable sessions, then resume them
        # all and hold unbounded live sockets past max_connections_per_user.
        # Check before taking the lock so can_user_connect() may do its (local
        # or Valkey) count read without deadlocking on this lock.
        old_conn_id = session.connection_id
        old_conn_is_live = bool(old_conn_id and old_conn_id in self._connections)
        if not old_conn_is_live and not self.can_user_connect(session.user_id):
            logger.warning(
                f"User {session.user_id} exceeded max connections; resume rejected"
            )
            return None

        with self._lock:
            old_conn_id = session.connection_id
            if old_conn_id and old_conn_id in self._connections:
                old_conn = self._connections[old_conn_id]
                old_conn.set_disconnected()

            session.connection_id = connection.connection_id
            # The client may have received fewer events than this worker has
            # persisted. Never move the session sequence backwards during a
            # cross-worker resume; the next dispatch must remain monotonic.
            session.sequence = max(session.sequence, int(sequence))
            session.update_activity()

            user_conns = self._user_connections.setdefault(session.user_id, set())
            if old_conn_id:
                user_conns.discard(old_conn_id)
            user_conns.add(connection.connection_id)

        # Never hand a resuming client the cached intent mask verbatim. A
        # persisted/stale session can carry privileged bits (or a corrupted
        # value) that IDENTIFY would reject; strip them fail-closed here so a
        # RESUME cannot re-escalate privileges. Heal the cached value too, so
        # the sanitized mask is what gets persisted for sibling workers.
        resume_intents = _safe_resume_intents(session.intents)
        session.intents = resume_intents
        # Heal the replay buffer too: a stale session can hold frames recorded
        # under privileged intents that the resumed connection no longer has.
        session.prune_replay_events_for_intents(resume_intents)
        connection.set_identified(session.user_id, session_id, resume_intents)
        connection.sequence = session.sequence

        # Valkey I/O outside the lock. The NEW connection id must be
        # registered immediately: without this, get_user_connection_count
        # undercounts after every resume and the global 5-connection cap
        # could be bypassed via repeated resume churn.
        self._valkey_register_connection(session.user_id, connection.connection_id)
        if old_conn_id and old_conn_id != connection.connection_id:
            # Drop the OLD member from the user's global set right away so
            # the count stays accurate even before the old socket's cleanup
            # runs. Deliberately srem-only (no decr): stats:total_connections
            # stays balanced because the old socket's eventual
            # remove_connection() performs the matching decr for the incr
            # this resume just issued for the new connection id.
            self._valkey_drop_connection_member(session.user_id, old_conn_id)

        self._persist_session(session)
        return session

    def _valkey_drop_connection_member(self, user_id: int, connection_id: str) -> None:
        """Remove a connection from the user's global set WITHOUT decr.

        Used when resuming: the old connection id must stop counting
        toward the per-user cap immediately, but its
        ``stats:total_connections`` decrement still belongs to the old
        socket's own cleanup path.
        """
        if not valkey_available():
            return
        client = get_valkey_client()
        if not client:
            return
        try:
            # CROSS-WORKER RESUME: the set member was registered by the
            # worker that owned the OLD connection as
            # ``<that worker's id>:<conn_id>`` — not ours. Read the owner
            # from conn:{id}:info (written at registration time) and srem
            # under THAT prefix; otherwise the stale member survives until
            # the 24h TTL and inflates get_user_connection_count past the
            # per-user connection cap. Fall back to our own worker id when
            # the info hash has already expired (same-worker case).
            owner_worker = client.hget(f"conn:{connection_id}:info", "worker")
            if isinstance(owner_worker, bytes):
                owner_worker = owner_worker.decode("utf-8", "replace")
            if not owner_worker:
                owner_worker = self._worker_id
            client.srem(
                f"user:{user_id}:connections",
                f"{owner_worker}:{connection_id}",
            )
        except Exception as e:
            logger.debug(f"Valkey connection member drop failed: {e}")

    def record_event(
        self,
        session_id: str,
        event: Dict[str, Any],
        *,
        authz_scope: Optional[tuple] = None,
    ) -> None:
        """
        Record an event for potential replay.

        Args:
            session_id: Session ID
            event: Event data with sequence number
            authz_scope: Optional ``(server_id, channel_id)`` routing scope the
                frame was authorised for at dispatch time. It is stamped onto a
                *copy* of the frame under ``REPLAY_SCOPE_KEY`` so RESUME can
                re-check the viewer's current membership/visibility before
                replaying a private channel event. Storing it on a copy keeps
                the live frame (and its wire shape) untouched.
        """
        session = self.get_session(session_id)
        if session:
            recorded = dict(event)
            if authz_scope is not None:
                recorded[REPLAY_SCOPE_KEY] = [
                    _replay_scope_id(authz_scope[0]),
                    _replay_scope_id(authz_scope[1]),
                ]
            session.add_replay_event(recorded)
            event_sequence = event.get("s")
            if isinstance(event_sequence, (int, float)):
                session.sequence = max(session.sequence, int(event_sequence))
            session.update_activity()
            self._persist_session(session)

    def get_replay_events(
        self, session_id: str, after_sequence: int
    ) -> List[Dict[str, Any]]:
        """
        Get events to replay after resume.

        Args:
            session_id: Session ID
            after_sequence: Last sequence received by client

        Returns:
            List of events to replay
        """
        session = self.get_session(session_id)
        if not session:
            return []
        return session.get_replay_events(after_sequence)

    def get_connections_for_users(self, user_ids: List[int]) -> List[Connection]:
        """
        Get all connections for a list of users.

        Duplicate ids are collapsed before lookup, and a connection is returned
        at most once even if it appears under more than one requested user.
        Direct dispatcher callers (gateway opcodes, typing/presence relays)
        bypass ``EventRouter``'s recipient dedupe, so without this guard a
        repeated user id fanned the same frame out to the same socket more
        than once: extra sequence numbers, duplicate replay entries and wasted
        serialization/send work for every recipient. First-seen order is kept
        so fan-out remains deterministic.

        Args:
            user_ids: List of user IDs

        Returns:
            List of distinct authenticated connections
        """
        connections: List[Connection] = []
        seen_users: Set[int] = set()
        seen_connections: Set[str] = set()
        with self._lock:
            for user_id in user_ids:
                if user_id in seen_users:
                    continue
                seen_users.add(user_id)
                conn_ids = self._user_connections.get(user_id, set())
                for cid in conn_ids:
                    if cid in seen_connections:
                        continue
                    conn = self._connections.get(cid)
                    if conn and conn.is_authenticated:
                        seen_connections.add(cid)
                        connections.append(conn)
        return connections

    def cleanup_stale_sessions(self) -> int:
        """
        Remove stale sessions that have timed out.

        Returns:
            Number of sessions removed
        """
        now = time.monotonic()
        timeout_seconds = self._session_timeout_ms / 1000
        stale_ids = []

        with self._lock:
            for session_id, session in self._sessions.items():
                if now - session.last_activity > timeout_seconds:
                    if session.connection_id not in self._connections:
                        stale_ids.append(session_id)

            for session_id in stale_ids:
                self._sessions.pop(session_id, None)

        # Drop the shared copies too, outside the lock, so a sibling worker
        # cannot rehydrate a session just declared stale here.
        for session_id in stale_ids:
            self._delete_shared_session(session_id)

        return len(stale_ids)

    def clear_all_global_sessions(self) -> None:
        """Clear all connections belonging to this worker from Valkey (on shutdown)."""
        if not valkey_available():
            return

        client = get_valkey_client()
        if not client:
            return

        try:
            # 1. Get all identified users for this worker
            with self._lock:
                users = list(self._user_connections.keys())

            # 2. Remove each connection from Valkey
            for user_id in users:
                conn_ids = self._user_connections.get(user_id, set())
                for cid in list(conn_ids):
                    self._valkey_unregister_connection(user_id, cid)

            logger.info(f"Cleared all Valkey sessions for worker {self._worker_id}")
        except Exception as e:
            logger.debug(f"Valkey global session cleanup failed: {e}")

    def get_all_connections(self) -> List[Connection]:
        """
        Get all connections across all users.

        Returns:
            List of all connections (authenticated and unauthenticated)
        """
        with self._lock:
            return list(self._connections.values())

    def get_stats(self) -> Dict[str, int]:
        """Get session manager statistics."""
        with self._lock:
            active_connections = sum(
                1
                for c in self._connections.values()
                if c.state == ConnectionState.READY
            )
            return {
                "total_connections": len(self._connections),
                "active_connections": active_connections,
                "total_sessions": len(self._sessions),
                "unique_users": len(self._user_connections),
            }
