# Relationships

Manage friends and blocks in Plexichat.

## Overview

The relationships module provides friend requests, bidirectional friendships, block and unblock, relationship status queries, and mutual information. It automatically resolves conflicts when blocks occur and integrates with the servers module for mutual server counts.

Use this module to implement friend lists, block lists, and relationship-aware features such as direct message access control.

## Before you begin

Call `setup` once at startup before using any other function. Pass a connected database instance and, optionally, auth and servers modules.

```python
from plexichat.src.core.database import Database
from plexichat.src.core import auth
from plexichat.src.core import servers
from plexichat.src.core import relationships

db = Database()
db.connect()

auth.setup(db)
servers.setup(db, auth)

relationships.setup(db, auth, servers)
```

`__init__.py:101` stores the manager singleton. All public functions delegate to `RelationshipManager` (`manager/composer.py`) through `_get_manager` (`__init__.py:119`), which raises `RuntimeError` if you call it before setup.

## Send and respond to friend requests

### Send a friend request

```python
from plexichat.src.core import relationships

request = relationships.send_friend_request(
    sender_id=user1_id,
    recipient_id=user2_id,
    message="Hey, let's be friends!",
)
```

### Accept, decline, or cancel a request

```python
# Recipient accepts or declines
request = relationships.accept_friend_request(user2_id, request.id)
request = relationships.decline_friend_request(user2_id, request.id)

# Sender cancels an outgoing request
request = relationships.cancel_friend_request(user1_id, request.id)

# Look up a request by ID
request = relationships.get_friend_request(request.id)

# List pending requests
incoming = relationships.get_pending_requests_incoming(user_id, limit=100)
outgoing = relationships.get_pending_requests_outgoing(user_id, limit=100)
```

Request lifecycle is defined in `models.py:21`: `PENDING`, `ACCEPTED`, `DECLINED`, `CANCELLED`. Accepting a pending request creates a bidirectional friendship.

## Manage friends

```python
# List friends
friends = relationships.get_friends(user_id, limit=100)

# Get only IDs
friend_ids = relationships.get_friend_ids(user_id)

# Remove a friend
relationships.remove_friend(user_id, friend_id)
```

Friendship records are bidirectional and stored in `rel_friends`. `remove_friend` deletes both directions.

## Block users

Blocking is one-way. When you block a user, the module automatically handles cleanup (see [Blocking behavior](#blocking-behavior)).

```python
# Block a user
block = relationships.block_user(blocker_id=user_id, blocked_id=target_id, reason="Spam")

# Unblock
relationships.unblock_user(user_id, target_id)

# Query block state
blocked = relationships.get_blocked_users(user_id, limit=100)
blocked_ids = relationships.get_blocked_user_ids(user_id)
all_blocked = relationships.get_all_blocked_ids(user_id)
is_blocked = relationships.is_blocked(blocker_id, blocked_id)
either_blocked = relationships.is_blocked_by_either(user1_id, user2_id)
```

## Check relationship status

Use `get_relationship` to get the status between two users, or `get_all_relationships` for a complete overview.

```python
rel = relationships.get_relationship(user_id, target_id)

if rel.status == relationships.RelationshipStatus.FRIEND:
    print("You are friends")
elif rel.status == relationships.RelationshipStatus.BLOCKED:
    print("You have blocked this user")
elif rel.status == relationships.RelationshipStatus.PENDING_INCOMING:
    print("They sent you a request")
elif rel.status == relationships.RelationshipStatus.PENDING_OUTGOING:
    print("You sent them a request")
else:
    print("No relationship")

# Get all relationships at once
all_rels = relationships.get_all_relationships(user_id)
```

### Status values

Defined in `models.py:11`:

| Status | Description |
|--------|-------------|
| `NONE` | No relationship. |
| `FRIEND` | Users are bidirectional friends. |
| `BLOCKED` | You have blocked the target. |
| `PENDING_INCOMING` | Target sent you a friend request. |
| `PENDING_OUTGOING` | You sent the target a friend request. |

## Get mutual information

```python
# Mutual friends
mutual_ids = relationships.get_mutual_friends(user1_id, user2_id)
count = relationships.get_mutual_friend_count(user1_id, user2_id)

# Mutual servers (requires servers module)
mutual_servers = relationships.get_mutual_servers(user1_id, user2_id)
server_count = relationships.get_mutual_server_count(user1_id, user2_id)

# All mutual information at once
info = relationships.get_mutual_info(user1_id, user2_id)
print(f"Mutual friends: {info.mutual_friend_count}")
print(f"Mutual servers: {info.mutual_server_count}")
```

`MutualInfo` is defined in `models.py:75`. When the servers module is not configured, mutual server queries return an empty list.

## Blocking behavior

When you block a user (`manager/blocking.py`), the module does the following atomically:

1. Removes any existing friendship in both directions.
2. Declines pending friend requests in either direction.
3. Prevents the blocked user from sending new friend requests. Attempts raise `UserBlockedError`.
4. Supports DM filtering when the messaging module checks `is_blocked_by_either`.

## Handle errors

All errors inherit from `RelationshipError` (`__init__.py:42`, `exceptions.py`):

| Exception | When it occurs |
|-----------|----------------|
| `UserNotFoundError` | User does not exist. |
| `SelfRelationshipError`, `CannotBlockSelfError` | You target yourself. |
| `FriendRequestNotFoundError` | Request ID does not exist or you are not the recipient/sender. |
| `FriendRequestExistsError` | A pending request already exists between these users. |
| `AlreadyFriendsError` | Users are already friends. |
| `NotFriendsError` | Users are not friends. |
| `UserBlockedError` | Action blocked because either user has blocked the other. |
| `AlreadyBlockedError` | User already blocked. |
| `NotBlockedError` | User not blocked. |

Example:

```python
from plexichat.src.core.relationships import (
    UserBlockedError,
    AlreadyFriendsError,
    FriendRequestExistsError,
)

try:
    relationships.send_friend_request(user_id, target_id)
except UserBlockedError:
    print("Cannot send request: blocked")
except AlreadyFriendsError:
    print("Already friends")
except FriendRequestExistsError:
    print("Request already pending")
```

## Data models

Defined in `models.py`:

* `Relationship` (`models.py:31`) — `user_id`, `target_user_id`, `status`, `created_at`, `updated_at`.
* `FriendRequest` (`models.py:42`) — `id`, `sender_id`, `recipient_id`, `status`, `message`, `created_at`, `updated_at`.
* `BlockedUser` (`models.py:55`) — `id`, `blocker_id`, `blocked_id`, `reason`, `created_at`.
* `Friend` (`models.py:66`) — `id`, `user_id`, `friend_id`, `created_at`.
* `MutualInfo` (`models.py:76`) — `mutual_friends`, `mutual_friend_count`, `mutual_servers`, `mutual_server_count`.

### Database schema

Tables are prefixed with `rel_` (`schema.py`):

* `rel_friends` — Bidirectional friendship records.
* `rel_friend_requests` — Friend request history.
* `rel_blocked` — Block records.

## Test the module

```bash
pytest src/tests/relationships/ -v
```
