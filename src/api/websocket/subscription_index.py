"""Indexed lookup for connection-scoped fabric subscriptions.

Before: deciding whether a user's *last* tab had unsubscribed from an
artifact/workbook required scanning every connection in the process::

    any(rid in held and users.get(conn) == uid
        for conn, held in self._connection_subs.items())

With thousands of sockets this made a single unsubscribe (now called on every
artifact/sum op for permission re-authorization) O(total connections). Full
per-user teardown also walked every connection in ``unsubscribe_all``.

After: a small index counts, for each ``(user_id, resource_id)`` pair, how many
of that user's connections still hold the subscription, and maps each user to
their connection ids. The "last tab" check becomes an O(1) dict lookup and
teardown touches only that user's connections; multi-tab semantics are
unchanged.
"""

from typing import Dict, Set, Tuple


class SubscriptionIndex:
    """Count live connection holds and index connections by user."""

    def __init__(self) -> None:
        self._counts: Dict[Tuple[int, int], int] = {}
        self._user_connections: Dict[int, Set[str]] = {}

    def add_connection(self, user_id: int, connection_id: str) -> None:
        """Record that ``connection_id`` belongs to ``user_id``."""
        self._user_connections.setdefault(int(user_id), set()).add(connection_id)

    def discard_connection(self, user_id: int, connection_id: str) -> None:
        """Forget a connection on teardown; no-op if already gone."""
        conns = self._user_connections.get(int(user_id))
        if conns is not None:
            conns.discard(connection_id)
            if not conns:
                self._user_connections.pop(int(user_id), None)

    def connections_for(self, user_id: int) -> Set[str]:
        """Connection ids belonging to ``user_id`` (copy)."""
        return set(self._user_connections.get(int(user_id), set()))

    def add(self, user_id: int, resource_id: int) -> None:
        """Record one more connection hold on a user/resource pair."""
        key = (int(user_id), int(resource_id))
        self._counts[key] = self._counts.get(key, 0) + 1

    def discard(self, user_id: int, resource_id: int) -> bool:
        """Drop one connection hold; return True if there was one to drop."""
        key = (int(user_id), int(resource_id))
        count = self._counts.get(key, 0)
        if count <= 0:
            return False
        if count == 1:
            del self._counts[key]
        else:
            self._counts[key] = count - 1
        return True

    def count(self, user_id: int, resource_id: int) -> int:
        """Number of the user's connections subscribed to ``resource_id``."""
        return self._counts.get((int(user_id), int(resource_id)), 0)

    def drop_user(self, user_id: int) -> Set[str]:
        """Forget a user entirely; return their connection ids for cleanup."""
        uid = int(user_id)
        conns = self._user_connections.pop(uid, set())
        for key in [key for key in self._counts if key[0] == uid]:
            del self._counts[key]
        return conns
