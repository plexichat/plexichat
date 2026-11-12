"""Authenticated PlexiStash metadata routes package.

Compatibility shim: the former monolithic ``src.api.routes.suite.stash``
module is now a package. ``router`` and every handler are re-exported here,
so route registration and direct imports keep working.

The route modules are imported in the order the handlers were declared in the
monolith. FastAPI matches routes in registration order, so that order is part
of the behavior, not a style choice.
"""

from .helpers import (
    _error as _error,
    _manager as _manager,
    _package as _package,
    _require_stash_capability as _require_stash_capability,
    resolve_manager as resolve_manager,
)
from .router import router

from . import items as items
from . import uploads as uploads
from . import item_detail as item_detail
from . import versions as versions
from . import grants as grants
from . import collections as collections
from . import version_diff as version_diff
from . import share_links as share_links

from .uploads import (
    MAX_UPLOAD_DECODED_BYTES as MAX_UPLOAD_DECODED_BYTES,
    MAX_UPLOAD_ENCODED_LEN as MAX_UPLOAD_ENCODED_LEN,
)
from src.core.artifacts.capabilities import (
    get_capability as get_capability,
    is_usable as is_usable,
)

from .items import (
    list_items as list_items,
    create_item as create_item,
)
from .uploads import (
    upload_file as upload_file,
)
from .item_detail import (
    get_item as get_item,
    update_item as update_item,
    pin_item as pin_item,
    delete_item as delete_item,
    restore_item as restore_item,
)
from .versions import (
    list_versions as list_versions,
    create_version as create_version,
)
from .grants import (
    grant_item as grant_item,
    create_download_ticket as create_download_ticket,
    redeem_download_ticket as redeem_download_ticket,
    revoke_grant as revoke_grant,
)
from .collections import (
    list_collections as list_collections,
    create_collection as create_collection,
    grant_collection as grant_collection,
    list_collection_grants as list_collection_grants,
    revoke_collection_grant as revoke_collection_grant,
)
from .version_diff import (
    diff_versions as diff_versions,
)
from .share_links import (
    create_share_link as create_share_link,
    list_share_links as list_share_links,
    revoke_share_link as revoke_share_link,
    resolve_share_link as resolve_share_link,
    share_link_download as share_link_download,
)

__all__ = ["router"]
