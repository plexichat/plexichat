"""Shared member surface for the stash manager mixins.

The manager implementation is split across ``stash_manager_*.py`` mixin
modules composed by ``StashManager``. Each mixin relies on helpers and state
defined on a sibling mixin or on ``BaseManager``; this protocol declares that
shared surface so every mixin type-checks against it (same pattern as the
voice manager protocol). Real implementations always win over these
declarations: in the composed ``StashManager`` MRO every mixin precedes this
class.
"""

from typing import Any, Callable

from src.core.base import SnowflakeID


class StashManagerProtocol:
    # State owned by BaseManager / StashManagerBase construction. Members
    # provided by BaseManager are declared as annotations only so the
    # protocol never shadows the real implementation in the composed MRO.
    _db: Any = None
    _servers: Any = None
    _media: Any = None
    _generate_id: Callable[[], SnowflakeID]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def _audit(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _authorize(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _can_administer_server(self, user_id: int, server_id: int) -> bool:
        raise NotImplementedError

    def _can_expose_storage(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _can_expose_storage_batch(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _can_grant_collection(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _can_manage_server(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _collection_ancestor(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _collection_descendant(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _collection_grant_permission(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _collection_grantee_ids(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _collection_granted_item_ids(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _collection_row(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _decode(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _decode_text(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _grant_permission(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _index_item_text(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _is_member(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _item_grants(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _json(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _media_bytes(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _project_item_after_mutation(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _resolve_media_object(self, media_object_id: Any, owner_id: int) -> Any:
        raise NotImplementedError

    def _resolve_share_link_row(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _row_to_item(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _ticket_target(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _validate_collection(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _validate_link_metadata(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _validate_scope(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _version_dict(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError
