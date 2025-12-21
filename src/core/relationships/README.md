# Relationships Module

Friend and block management system for PlexiChat API supporting friend requests, blocking, and mutual information queries.

## Features

- Friend requests (send, accept, decline, cancel)
- Block/unblock users
- Relationship states: none, friend, blocked, pending_incoming, pending_outgoing
- Get friends list
- Get blocked users list
- Get pending requests (incoming and outgoing)
- Check relationship between two users
- Mutual friends calculation
- Mutual servers calculation (integrates with servers module)
- Auto-decline pending requests when blocking
- Auto-remove friendship when blocking

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import servers
from src.core import relationships

# Initialize database
db = Database()
db.connect()

# Initialize auth first
auth.setup(db)

# Initialize servers (optional, for mutual servers)
servers.setup(db, auth)

# Initialize relationships
relationships.setup(db, auth, servers)
```

## Usage

### Friend Requests

```python
from src.core import relationships

# Send a friend request
request = relationships.send_friend_request(
    sender_id=user1_id,
    recipient_id=user2_id,
    message="Hey, let's be friends!"
)

# Accept a friend request
request = relationships.accept_friend_request(user2_id, request.id)

# Decline a friend request
request = relationships.decline_friend_request(user2_id, request.id)

# Cancel a sent friend request
request = relationships.cancel_friend_request(user1_id, request.id)

# Get pending incoming requests
incoming = relationships.get_pending_requests_incoming(user_id)

# Get pending outgoing requests
outgoing = relationships.get_pending_requests_outgoing(user_id)
```

### Friends Management

```python
# Get friends list
friends = relationships.get_friends(user_id)

# Get friend IDs only
friend_ids = relationships.get_friend_ids(user_id)

# Remove a friend (unfriend)
relationships.remove_friend(user_id, friend_id)
```

### Blocking

```python
# Block a user
block = relationships.block_user(
    blocker_id=user_id,
    blocked_id=target_id,
    reason="Spam"
)

# Unblock a user
relationships.unblock_user(user_id, target_id)

# Get blocked users list
blocked = relationships.get_blocked_users(user_id)

# Check if user is blocked
is_blocked = relationships.is_blocked(blocker_id, blocked_id)

# Check if either user has blocked the other
either_blocked = relationships.is_blocked_by_either(user1_id, user2_id)
```

### Relationship Status

```python
# Get relationship between two users
rel = relationships.get_relationship(user_id, target_id)

if rel.status == relationships.RelationshipStatus.FRIEND:
    print("You are friends!")
elif rel.status == relationships.RelationshipStatus.BLOCKED:
    print("You have blocked this user")
elif rel.status == relationships.RelationshipStatus.PENDING_INCOMING:
    print("This user sent you a friend request")
elif rel.status == relationships.RelationshipStatus.PENDING_OUTGOING:
    print("You sent this user a friend request")
else:
    print("No relationship")
```

### Mutual Information

```python
# Get mutual friends
mutual_friends = relationships.get_mutual_friends(user1_id, user2_id)

# Get mutual friend count
count = relationships.get_mutual_friend_count(user1_id, user2_id)

# Get mutual servers (requires servers module)
mutual_servers = relationships.get_mutual_servers(user1_id, user2_id)

# Get all mutual info at once
info = relationships.get_mutual_info(user1_id, user2_id)
print(f"Mutual friends: {info.mutual_friend_count}")
print(f"Mutual servers: {info.mutual_server_count}")
```

## Relationship States

| Status | Description |
|--------|-------------|
| none | No relationship between users |
| friend | Users are friends |
| blocked | User has blocked the target |
| pending_incoming | Target sent a friend request to user |
| pending_outgoing | User sent a friend request to target |

## Blocking Behavior

When a user blocks another:
1. Any existing friendship is removed
2. Any pending friend requests (in either direction) are auto-declined
3. The blocked user cannot send new friend requests
4. DM creation is prevented (when integrated with messaging)

## Error Handling

All relationship errors inherit from `RelationshipError`:

```python
from src.core.relationships import (
    RelationshipError,
    UserNotFoundError,
    SelfRelationshipError,
    FriendRequestNotFoundError,
    FriendRequestExistsError,
    AlreadyFriendsError,
    NotFriendsError,
    UserBlockedError,
    AlreadyBlockedError,
    NotBlockedError,
    CannotBlockSelfError,
)

try:
    relationships.send_friend_request(user_id, target_id)
except UserBlockedError as e:
    print(f"Cannot send request: blocked")
except AlreadyFriendsError:
    print("Already friends!")
except FriendRequestExistsError:
    print("Request already sent")
```

## Database Schema

Tables (prefixed with `rel_`):
- `rel_friends` - Bidirectional friendship records
- `rel_friend_requests` - Friend request history
- `rel_blocked` - Block records

## Testing

```bash
pytest src/tests/relationships/ -v
```
