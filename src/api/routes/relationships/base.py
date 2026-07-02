"""
Base class for relationship route handlers.

Provides shared helpers (event dispatch, cache invalidation) used by all
mixins via multiple inheritance.
"""

from typing import Protocol

from src.core.database import invalidate_pattern


class RelationshipBaseProtocol(Protocol):
    """Protocol describing the shared interface expected by relationship mixins."""

    async def _dispatch_relationship_event(
        self, event_type: str, user_id: int, target_id: int, data: dict
    ) -> None: ...

    def _invalidate_relationship_list_cache(self, *user_ids: int) -> None: ...


class RelationshipsBase:
    async def _dispatch_relationship_event(
        self, event_type: str, user_id: int, target_id: int, data: dict
    ) -> None:
        """Dispatch relationship events via WebSocket."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if ws_is_setup():
                dispatcher = get_dispatcher()
                event = Event(
                    event_type=EventType.RELATIONSHIP_ADD
                    if event_type == "add"
                    else EventType.RELATIONSHIP_REMOVE,
                    data=data,
                )
                await dispatcher.dispatch_event(event, [user_id])
        except Exception as e:
            import utils.logger as logger

            logger.debug(f"Failed to dispatch relationship event: {e}")

    def _invalidate_relationship_list_cache(self, *user_ids: int) -> None:
        """Invalidate cached relationship listings for each supplied user ID."""
        prefix = "relationships_api"
        seen_ids = set()

        for user_id in user_ids:
            normalized_id = int(user_id)
            if normalized_id in seen_ids:
                continue
            seen_ids.add(normalized_id)
            invalidate_pattern(f"{prefix}:current_user:*:{normalized_id}")
