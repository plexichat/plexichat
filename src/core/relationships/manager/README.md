# Relationship Manager

Manages user-to-user relationships including friend requests, friendships, blocking, and mutual information queries. Built as a mixin-based composition assembled via MRO.

## Overview

The relationship manager provides the full lifecycle for interpersonal connections: sending/accepting/declining friend requests, managing friend lists, blocking/unblocking users, and computing mutual friends and servers. It enforces anti-harassment cooldowns, transactional consistency, and bidirectional cache invalidation across all operations.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                   RelationshipManager                    │
│  ┌────────────────┬──────────────────┬────────────────┐  │
│  │FriendRequests  │   FriendsMixin   │  BlockingMixin  │  │
│  │    Mixin       │                  │                 │  │
│  ├────────────────┼──────────────────┼────────────────┤  │
│  │RelationshipStatusMixin│MutualInfoMixin│             │  │
│  ├──────────────────────────────────────────────────────┤  │
│  │         RelationshipHelpersMixin (BaseManager)       │  │
│  └──────────────────────────────────────────────────────┘  │
│                           │                                │
│  ┌────────────────────────┴───────────────────────────┐   │
│  │  Database (rel_friends, rel_blocked,                │   │
│  │           rel_friend_requests, srv_members)         │   │
│  └────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `composer.py` | Assembles `RelationshipManager` from mixins via MRO |
| `protocol.py` | `RelationshipMixinProtocol` defining the shared interface |
| `helpers.py` | Shared validation, DB helpers, row converters, cache invalidation |
| `friend_requests.py` | Send, accept, decline, cancel friend requests with cooldown enforcement |
| `friends.py` | Get friends list, friend IDs (cached), remove friend |
| `blocking.py` | Block/unblock users with cascading friendship removal |
| `status.py` | Get relationship status between two users, aggregate relationship view |
| `mutual.py` | Mutual friends, mutual servers, friend suggestions |

## Usage

### Send a friend request

```python
request = rel_manager.send_friend_request(
    sender_id=user_id,
    recipient_id=target_id,
    message="Let's be friends!",
)
```

### Accept / decline / cancel

```python
rel_manager.accept_friend_request(user_id, request_id)
rel_manager.decline_friend_request(user_id, request_id)
rel_manager.cancel_friend_request(user_id, request_id)
```

### Get relationship status

```python
rel = rel_manager.get_relationship(user_id, target_id)
# rel.status: RelationshipStatus.FRIEND | BLOCKED | PENDING_INCOMING | ...

status = rel_manager.get_relationship_status(user_id, target_id)
```

### Block / unblock

```python
rel_manager.block_user(blocker_id, blocked_id, reason="Spam")
rel_manager.unblock_user(blocker_id, blocked_id)
```

### Mutual information

```python
mutual = rel_manager.get_mutual_info(user_id, target_id)
# mutual.mutual_friends, mutual.mutual_servers, mutual.mutual_friend_count

suggestions = rel_manager.get_suggested_friends(user_id, limit=10)
```

### Get all relationships (cached)

```python
all_rels = rel_manager.get_all_relationships(user_id)
# {"friends": [...], "pending_incoming": [...], "pending_outgoing": [...], "blocked": [...]}
```

## Configuration

- `FRIEND_REQUEST_RESEND_COOLDOWN_SECONDS` (60s): Minimum delay before re-sending a friend request to the same user after decline/cancel.
- `MAX_FRIEND_REQUEST_MESSAGE_LENGTH` (256): Maximum characters in a friend request message.
- Cache TTLs: `get_friend_ids` and `get_blocked_user_ids` use 300s TTL; `get_all_relationships` uses 60s TTL.

## Related Code

- Relationship models: `src/core/relationships/models.py`
- Relationship exceptions: `src/core/relationships/exceptions.py`
- Base manager: `src/core/base.py`
- Cache module: `src/core/database/cache/__init__.py`
- DM block enforcement: `src/core/messaging/services/message/send.py:403`
