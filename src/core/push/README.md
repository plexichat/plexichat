# Push Notification Module

Mobile push notifications via FCM/APNs with token management.

## Files

- `__init__.py` - Module exports (`PushManager`)
- `manager.py` - Token management and push delivery

## Key Classes

### `PushManager`

Handles registering, updating, and invalidating push notification tokens, as well as preparing and sending push payloads to Firebase Cloud Messaging and Apple Push Notification service.

- **Token registration** - Up to 10 tokens per user across ios/android/web platforms
- **Push delivery** - Prepares FCM/APNs payloads with message, badge, and sound
- **Token cleanup** - Automatic invalidation of expired/unresponsive tokens
- **Auto-detection** - Checks for FCM/APNs credentials at startup

### Supported Platforms

- `ios` - Apple Push Notification service (APNs)
- `android` - Firebase Cloud Messaging (FCM)
- `web` - Web Push API

### Usage

```python
from src.core.push import PushManager

mgr = PushManager(db, notifications_module)

# Register a device token
token = mgr.register_token(user_id=123, token="device_token", platform="android")

# Send a push notification
mgr.send_push(user_id=123, title="New message", body="You have a new DM")

# Unregister a token
mgr.unregister_token(user_id=123, token_id=456)
```
