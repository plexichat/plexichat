# Server discovery

Manage public server listings, categories, and verification for Plexichat.

## Overview

The discovery subsystem powers the public server directory. It lets server owners list and unlist servers, organize them by category and tags, surface them through sorted queries, and verify them for trust signals.

Use discovery when you need to browse public servers, categorize listings, or handle listing lifecycle operations such as bumping and verification.

The discovery code lives in `plexichat/src/core/search/discovery/` and is exposed through the top-level `search` module (`src/core/search/__init__.py:99`).

## Architecture

| File | Class | Responsibility |
|------|-------|----------------|
| `manager.py` | `DiscoveryManager` | Listing, unlisting, bumping, and querying listings. Owns configuration and permission checks. |
| `categories.py` | `CategoryManager` | Category cache, validation, and server-count aggregation. |
| `verification.py` | `VerificationManager` | Verification level assignment and eligibility checks. |

`DiscoveryManager` (`manager.py:23`) composes `CategoryManager` (`manager.py:29`) and `VerificationManager` (`manager.py:30`). All three are instantiated per search manager and share the same database handle.

## Before you begin

Discovery is initialized as part of `search.setup`. You do not call discovery setup directly.

```python
from plexichat.src.core.database import Database
from plexichat.src.core import auth, messaging, servers, search

db = Database()
db.connect()

auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
search.setup(db, auth, messaging, servers)
```

The servers module is required for permission checks. If it is not configured, `list_server`, `unlist_server`, and `bump_server` fail closed with `SearchPermissionError` (`manager.py:347`, `manager.py:376`).

## List and unlist servers

### List a server

```python
from plexichat.src.core import search

listing = search.list_server(
    user_id=owner_id,
    server_id=123,
    category="gaming",
    description="A friendly gaming community",
    tags=["minecraft", "survival", "friendly"],
)
# Returns ServerListing (src/core/search/models.py:182)
```

Behavior (`manager.py:52`):

1. Verifies `server.manage` permission for the caller.
2. Validates that `category` exists via `CategoryManager.validate_category` (`categories.py:43`).
3. Checks that the server has at least `min_members_for_listing` members (default 10).
4. Truncates `tags` to `max_tags` (default 10).
5. Creates a new row or updates the existing listing, then returns a `ServerListing`.

### Unlist a server

```python
search.unlist_server(user_id=owner_id, server_id=123)
```

Requires `server.manage`. Raises `ServerNotListedError` if the server is not listed (`manager.py:173`).

### Get a listing

```python
listing = search.list_public_servers(category="gaming", sort_by="member_count", limit=25)[0]
# Or fetch one server directly through the manager
from plexichat.src.core.search.discovery.manager import DiscoveryManager
# manager.get_listing(server_id) -> ServerListing | None (manager.py:310)
# manager.get_listings_bulk(server_ids) -> dict[int, ServerListing] (manager.py:434)
```

## Browse the directory

### List public servers

```python
servers = search.list_public_servers(category="gaming", sort_by="member_count", limit=25, offset=0)
# sort_by: "member_count" (default), "bumped_at", or "created_at" (manager.py:257)
```

Filters by `category` when provided, sorts by the selected column, and paginates with `limit` and `offset`. Results join `srv_servers` for name and icon (`manager.py:263`).

### Search servers

Use the search module's server search for full-text queries over name, description, and tags:

```python
results = search.search_servers(user_id=1, query="gaming", category="gaming", limit=25)
page = search.search_servers_page(user_id=1, query="gaming", limit=25)
```

## Manage categories

Categories are stored in `search_categories` and cached in memory via `CappedDict` (`categories.py:23`). Cache size is controlled by `valkey.cache_max_items` (or legacy `redis.cache_max_items`).

```python
# Get all categories with live server counts
categories = search.get_server_categories()
# Returns list[ServerCategory] (../models.py:171): id, name, description, icon, server_count

# Direct manager usage
from plexichat.src.core.search.discovery.categories import CategoryManager

cm = CategoryManager(db)
cm.get_all_categories()
cm.get_category("gaming")
cm.category_exists("gaming")
cm.validate_category("gaming")  # Raises CategoryNotFoundError if unknown
cm.get_category_server_count("gaming")
cm.update_category_counts()
cm.invalidate_cache()
```

`get_all_categories` (`categories.py:26`) sorts by `id`. `update_category_counts` (`categories.py:60`) aggregates counts from `search_server_listings` and is called before returning categories from the discovery manager (`manager.py:307`).

### Default categories

Configured under `search.discovery.categories`:

`gaming`, `music`, `entertainment`, `education`, `science`, `creative`, `social`, `sports`, `finance`, `other`.

## Bump servers

Bumping refreshes `bumped_at` and increments `bump_count`, affecting sort order when `sort_by="bumped_at"`.

```python
search.bump_server(user_id=1, server_id=123)
```

Rules (`manager.py:185`):

* Caller must be a member of the server. Otherwise returns `False` (legacy boolean API) instead of raising, to keep probing idempotent (`manager.py:196`).
* Returns `False` if the server is not listed.
* Enforces a cooldown of `bump_cooldown_hours` (default 4 hours). Raises `BumpCooldownError` with `cooldown_remaining` in milliseconds if too early (`manager.py:212`).
* Records each bump in `search_bump_history` (`manager.py:228`).

Permissions fail closed when the servers module is unavailable (`manager.py:376`).

## Verification

Set and query the verification level for a listed server.

```python
from plexichat.src.core.search.models import VerificationLevel

# Set level (admin)
search.verify_server(server_id=123, level=VerificationLevel.VERIFIED)

# Direct manager usage
from plexichat.src.core.search.discovery.verification import VerificationManager

vm = VerificationManager(db, servers)
vm.get_verification_level(server_id)       # -> VerificationLevel
vm.is_verified(server_id)                   # -> bool (true for HIGH and VERIFIED)
vm.check_verification_requirements(server_id, VerificationLevel.VERIFIED)
# -> {"eligible": bool, "missing": [str]}
```

Behavior (`verification.py:19`):

* Returns `False` if the server is not listed (idempotent no-op).
* Sets `is_verified` to true only for `HIGH` and `VERIFIED` (`verification.py:44`).
* Requirements (`verification.py:103`): `LOW` 10 members, `MEDIUM` 100, `HIGH` 500, `VERIFIED` 1000. `check_verification_requirements` returns missing requirements without raising.

| Level | Value | Members required |
|-------|-------|------------------|
| `NONE` | `none` | 0 |
| `LOW` | `low` | 10 |
| `MEDIUM` | `medium` | 100 |
| `HIGH` | `high` | 500 |
| `VERIFIED` | `verified` | 1000 |

## Handle errors

Exceptions are defined in `src/core/search/exceptions.py`:

| Exception | When it occurs |
|-----------|----------------|
| `SearchPermissionError` | You lack `server.manage` or `server.member`, or servers module is unavailable. |
| `CategoryNotFoundError` | Category does not exist (`categories.py:46`). Includes `category` attribute. |
| `MinimumMembersError` | Server below `min_members_for_listing` (`manager.py:81`). Includes `required` and `current`. |
| `ServerNotListedError` | Server not in `search_server_listings` (from `unlist_server`). |
| `BumpCooldownError` | Bump attempted before cooldown expires. Includes `cooldown_remaining` and `server_id`. |

Example:

```python
from plexichat.src.core.search import CategoryNotFoundError, MinimumMembersError
from plexichat.src.core.search import BumpCooldownError

try:
    search.list_server(user_id, server_id, category="unknown")
except CategoryNotFoundError as e:
    print(f"Unknown category: {e.category}")

try:
    search.bump_server(user_id, server_id)
except BumpCooldownError as e:
    print(f"Wait {e.cooldown_remaining // 1000} seconds")
```

## Configuration

Discovery keys under `search.discovery` in `config/config.yaml` (`manager.py:33`):

```yaml
search:
  discovery:
    min_members_for_listing: 10
    bump_cooldown_hours: 4
    max_tags: 10
    categories:
      - gaming
      - music
      - entertainment
      - education
      - science
      - creative
      - social
      - sports
      - finance
      - other
```

| Key | Default | Description |
|-----|---------|-------------|
| `min_members_for_listing` | `10` | Minimum members required to list a server. |
| `bump_cooldown_hours` | `4` | Hours before a server can be bumped again. |
| `max_tags` | `10` | Maximum tags stored per listing; extra tags are truncated. |

## Data models

Defined in `src/core/search/models.py`:

* `ServerListing` (`../models.py:182`) — `id`, `server_id`, `name`, `description`, `icon_url`, `banner_url`, `category`, `tags`, `member_count`, `online_count`, `verification_level`, `is_verified`, `is_partnered`, `listed_at`, `bumped_at`, `bump_count`.
* `ServerCategory` (`../models.py:171`) — `id`, `name`, `description`, `icon`, `server_count`.
* `VerificationLevel` (`../models.py:33`) — `NONE`, `LOW`, `MEDIUM`, `HIGH`, `VERIFIED`.

### Database tables

* `search_server_listings` — Public listings (joined with `srv_servers` for display).
* `search_categories` — Category definitions.
* `search_bump_history` — Bump audit log.
