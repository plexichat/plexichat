"""
Session management - Manages gateway sessions and connections.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Set, Any
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
from .intents import validate_intents


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
            self.replay_events = self.sanitize_replay_events(
                self.replay_events, self.max_replay_events
            )
        self.replay_bytes = sum(payload_size(event) for event in self.replay_events)

    @staticmethod
    def sanitize_replay_events(events: object, max_events: int) -> List[Dict[str, Any]]:
        """Return a bounded, well-formed replay list from untrusted ``events``.

        Drops non-dict entries and per-event oversized frames, and evicts the
        oldest entries until the list fits both ``max_events`` and the total
        ``MAX_SESSION_REPLAY_BYTES`` budget. Fail closed: anything that cannot
        be measured is dropped rather than replayed.
        """
        kept: List[Dict[str, Any]] = []
        total = 0
        limit = max(1, int(max_events))
        if not isinstance(events, (list, tuple)):
            return kept
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
                dropped = kept.pop(0)
                total -= payload_size(dropped)
            if total + size > MAX_SESSION_REPLAY_BYTES:
                continue
            kept.append(event)
            total += size
        return kept

    def add_replay_event(self, event: Dict[str, Any]) -> None:
        """Add an event to the replay buffer.

        The buffer is bounded by count (``max_replay_events``) and by the
        total ``MAX_SESSION_REPLAY_BYTES`` budget: once either is exceeded the
        oldest entry is evicted, so a long disconnect can never miss more than
        the window and a single session cannot amplify into an unbounded replay
        frame. Eviction is logged at WARNING so replay gaps are observable.
        """
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
        self.replay_bytes += size
        evicted = False
        while (
            self.replay_events
            and (
                len(self.replay_events) > self.max_replay_events
                or self.replay_bytes > MAX_SESSION_REPLAY_BYTES
            )
        ):
            dropped = self.replay_events.pop(0)
            self.replay_bytes -= payload_size(dropped)
            evicted = True
        if evicted and not getattr(self, "_replay_overflow_warned", False):
            self._replay_overflow_warned = True
            logger.warning(
                f"Session {self.session_id}: replay buffer overflow "
                f"(max {self.max_replay_events} events / "
                f"{MAX_SESSION_REPLAY_BYTES} bytes); oldest events evicted "
                f"and unrecoverable on resume"
            )

    def get_replay_events(self, after_sequence: int) -> List[Dict[str, Any]]:
        """Get events after a specific sequence number.

        Final backstop: re-check the per-event ceiling here so even a caller
        that mutates ``replay_events`` directly cannot leak an oversized frame
        to a resuming client.
        """
        return [
            event
            for event in self.replay_events
            if isinstance(event, dict)
            and event.get("s", 0) > after_sequence
            and payload_size(event) <= MAX_REPLAY_EVENT_BYTES
        ]

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
    ):
        """
        Initialize the session manager.

        Args:
            heartbeat_interval_ms: Heartbeat interval in milliseconds
            session_timeout_ms: Session timeout for resume in milliseconds
            max_connections_per_user: Maximum concurrent connections per user
        """
        self._heartbeat_interval_ms = heartbeat_interval_ms
        self._session_timeout_ms = session_timeout_ms
        self._max_connections_per_user = max_connections_per_user

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
                created_at_epoch=float(raw.get("created_at_epoch", last_epoch)),
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
        return shared

    def remove_session(self, session_id: str) -> Optional[Session]:
        """Remove a local and shared session."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if valkey_available():
            client = get_valkey_client()
            if client:
                try:
                    client.delete(self._session_key(session_id))
                except Exception as exc:
                    logger.debug(f"Valkey session removal failed: {exc}")
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

        connection.set_identified(session.user_id, session_id, session.intents)
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

    def record_event(self, session_id: str, event: Dict[str, Any]) -> None:
        """
        Record an event for potential replay.

        Args:
            session_id: Session ID
            event: Event data with sequence number
        """
        session = self.get_session(session_id)
        if session:
            session.add_replay_event(event)
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

        Args:
            user_ids: List of user IDs

        Returns:
            List of connections
        """
        connections = []
        with self._lock:
            for user_id in user_ids:
                conn_ids = self._user_connections.get(user_id, set())
                for cid in conn_ids:
                    conn = self._connections.get(cid)
                    if conn and conn.is_authenticated:
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
