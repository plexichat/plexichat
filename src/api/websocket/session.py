"""
Session management - Manages gateway sessions and connections.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Set, Any
import time
import secrets
import threading

from .connection import Connection, ConnectionState


@dataclass
class Session:
    """Represents a resumable gateway session."""

    session_id: str
    user_id: int
    sequence: int = 0
    intents: int = 0
    created_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    connection_id: Optional[str] = None
    replay_events: List[Dict[str, Any]] = field(default_factory=list)
    max_replay_events: int = 100

    def add_replay_event(self, event: Dict[str, Any]) -> None:
        """Add an event to the replay buffer."""
        self.replay_events.append(event)
        if len(self.replay_events) > self.max_replay_events:
            self.replay_events.pop(0)

    def get_replay_events(self, after_sequence: int) -> List[Dict[str, Any]]:
        """Get events after a specific sequence number."""
        return [e for e in self.replay_events if e.get("s", 0) > after_sequence]

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.monotonic()


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
        with self._lock:
            self._connections[connection.connection_id] = connection

    def remove_connection(self, connection_id: str) -> Optional[Connection]:
        """
        Remove a connection.

        Args:
            connection_id: Connection ID to remove

        Returns:
            Removed connection or None
        """
        with self._lock:
            connection = self._connections.pop(connection_id, None)
            if connection and connection.user_id:
                user_conns = self._user_connections.get(connection.user_id, set())
                user_conns.discard(connection_id)
                if not user_conns:
                    self._user_connections.pop(connection.user_id, None)
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
        """Get the number of connections for a user."""
        with self._lock:
            return len(self._user_connections.get(user_id, set()))

    def can_user_connect(self, user_id: int) -> bool:
        """Check if user can create a new connection."""
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

        connection.set_identified(user_id, session_id, intents)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> Optional[Session]:
        """Remove a session."""
        with self._lock:
            return self._sessions.pop(session_id, None)

    def can_resume_session(self, session_id: str, user_id: int) -> bool:
        """
        Check if a session can be resumed.

        Args:
            session_id: Session ID to check
            user_id: User ID attempting resume

        Returns:
            True if session can be resumed
        """
        session = self._sessions.get(session_id)
        if not session:
            return False

        if session.user_id != user_id:
            return False

        now = time.monotonic()
        timeout_seconds = self._session_timeout_ms / 1000
        if now - session.last_activity > timeout_seconds:
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
        session = self._sessions.get(session_id)
        if not session:
            return None

        with self._lock:
            old_conn_id = session.connection_id
            if old_conn_id and old_conn_id in self._connections:
                old_conn = self._connections[old_conn_id]
                old_conn.set_disconnected()

            session.connection_id = connection.connection_id
            session.update_activity()

            user_conns = self._user_connections.setdefault(session.user_id, set())
            if old_conn_id:
                user_conns.discard(old_conn_id)
            user_conns.add(connection.connection_id)

        connection.set_identified(session.user_id, session_id, session.intents)
        connection.sequence = sequence
        return session

    def record_event(self, session_id: str, event: Dict[str, Any]) -> None:
        """
        Record an event for potential replay.

        Args:
            session_id: Session ID
            event: Event data with sequence number
        """
        session = self._sessions.get(session_id)
        if session:
            session.add_replay_event(event)
            session.update_activity()

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
        session = self._sessions.get(session_id)
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
