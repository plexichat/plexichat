"""PlexiStash authorization contract.

This module intentionally does not implement or register permission checks yet.
It records the rules the eventual service must enforce consistently for list,
metadata, preview, download, mutation, share, and version endpoints.
"""

from typing import Final, Tuple

from .schema import STASH_PERMISSIONS


STASH_PERMISSION_NAMES: Final[Tuple[str, ...]] = STASH_PERMISSIONS


# Required evaluation order for the future service:
#
# 1. Authenticate the user and reject suspended/invalid sessions.
# 2. Resolve the opaque item ID; never accept a filesystem path.
# 3. Check item/collection ownership and explicit grants.
# 4. Resolve server-role grants only when the item is server-scoped.
# 5. Apply inherited grants and explicit revocations.
# 6. Validate expiring/shared-link state and requested permission.
# 7. Apply legal hold, deletion, and version state.
# 8. Record security-sensitive actions in the Stash audit stream.
#
# Discovery metadata and binary access are separate permissions. Seeing a file
# name never implies permission to download it. Download URLs must be short-lived
# and scope-checked at use time, not only when generated.
