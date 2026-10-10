# Push

Send mobile and web push notifications through FCM, APNs, and Web Push with token lifecycle management.

## Overview

The push module registers device tokens, encrypts them at rest, and delivers notifications to Firebase Cloud Messaging (Android), Apple Push Notification service (iOS), and the Web Push protocol (browsers). The `PushManager` class handles registration, delivery, and cleanup of invalid tokens.

Tokens are stored encrypted with a blind index for lookups. The manager auto-detects available push services at startup and migrates legacy plaintext rows.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Module exports. Exports `PushManager` and provides `setup()` / `_get_manager()`. |
| `manager.py` | Token management, encryption, and delivery to FCM, APNs, and Web Push. |
| `exceptions.py` | Push-specific exceptions such as `PermissionDeniedError`. |

## Before you begin

Configure at least one push service under `push_notifications` in your config:

* **FCM (Android):** `fcm_credentials_path` or `fcm_api_key`
* **APNs (iOS):** `apns_cert_path` or `apns_token_path`; for token-based auth also set `apns_team_id`, `apns_key_id`, `apns_bundle_id`, and `apns_p8_key`
* **Web Push (browsers):** `vapid_private_key` and `vapid_subject`

Install optional dependencies `firebase-admin` (FCM), `pywebpush` (Web Push), and `httpx[http2]` with `PyJWT` (APNs).

## Supported platforms

| Value | Service | Token format |
|-------|---------|--------------|
| `android` | Firebase Cloud Messaging | FCM registration token |
| `ios` | Apple Push Notification service | APNs device token |
| `web` | Web Push (VAPID) | Subscription endpoint URL |

## Use `PushManager`

### Set up the module

```python
from src.core.push import PushManager

mgr = PushManager(db, notifications_module)
```

Or use the module-level setup:

```python
from src.core import push

push.setup(db, notifications_module)
mgr = push._get_manager()
```

### Register a token

You can store up to 10 tokens per user. If the limit is reached, the oldest token is evicted. Re-registering an existing token updates it; registering a token that belongs to another user raises `PermissionDeniedError`.

```python
# Android / iOS
token = mgr.register_token(
    user_id=123,
    token="device_token",
    platform="android",
    device_id="pixel-7",
    app_version="2.4.0",
)

# Web Push — include VAPID keys
token = mgr.register_token(
    user_id=123,
    token="https://fcm.googleapis.com/wp/...",
    platform="web",
    p256dh="BNc...",
    auth="tBH...",
)
```

The returned dict is sanitized: raw `token`, `p256dh`, `auth`, and `token_index` are removed and replaced with a non-reversible `token_hint` (last 4 characters).

### Send a notification

`send_push` delivers to all of a user's devices, respects the user's `mobile_push` setting when a notifications module is configured, and removes tokens that the provider permanently rejects. Transient errors (network, auth, rate limit) leave the token intact.

```python
sent = mgr.send_push(
    user_id=123,
    title="New message",
    body="You have a new DM",
    data={"conversation_id": "456"},
    badge=3,
    sound="default",
    priority="high",
)
```

| Return value | Meaning |
|--------------|---------|
| `True` | Delivered successfully. |
| `False` | Token is permanently invalid; the caller deletes it. |
| `None` | Delivery skipped (missing config or transient error); the token is kept. |

### Remove a token

```python
# By raw token value (server-side, when you have the plaintext)
mgr.unregister_token(user_id=123, token="device_token")

# By row ID (client-side, using only token_hint)
mgr.unregister_token_by_id(user_id=123, token_id=456)
```

### Query tokens

```python
# All tokens for a user (decrypted, for internal delivery only — never return directly to clients)
tokens = mgr.get_user_tokens(user_id=123)

# Filter by platform
ios_tokens = mgr.get_user_tokens(user_id=123, platform="ios")
```

### Send to multiple users

```python
total = mgr.send_bulk_push(
    user_ids=[123, 456, 789],
    title="Server announcement",
    body="Maintenance at 02:00 UTC",
)
```

## Delivery details

* **FCM:** Uses `firebase-admin.messaging.send()`. Invalid or unregistered tokens are removed; other errors are treated as transient.
* **APNs:** Sends an HTTP/2 POST with an ES256 JWT (cached for up to one hour). A `410` or `BadDeviceToken` / `Unregistered` / `ExpiredToken` reason removes the token; other statuses are treated as transient. Supports `apns_use_sandbox` for development.
* **Web Push:** Uses `pywebpush` with `aes128gcm` encoding. A `404` or `410` from the push service removes the subscription; other errors are treated as transient.

## Security

* Tokens, `p256dh`, and `auth` values are encrypted at rest with an `ENC:v1` envelope. Lookups use a keyed blind index (`push_token` scope).
* API responses never include raw credentials. Use `_sanitize_token_record` semantics and expose only `token_hint`.
* Legacy plaintext rows are re-encrypted on startup.

## Error handling

| Condition | Result |
|-----------|--------|
| Unsupported platform | `ValueError` |
| Token already registered to another user | `PermissionDeniedError` |
| Missing VAPID/APNs/FCM config | Delivery returns `None`; token is kept |
