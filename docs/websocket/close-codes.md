# WebSocket Close Codes

How to handle different close codes when disconnected.

## Reconnectable Codes

These codes allow reconnection and session resume:

| Code | Name | Action |
|------|------|--------|
| 4000 | UNKNOWN_ERROR | Reconnect with backoff |
| 4001 | UNKNOWN_OPCODE | Reconnect immediately |
| 4002 | DECODE_ERROR | Reconnect immediately |
| 4003 | NOT_AUTHENTICATED | Reconnect and re-identify |
| 4005 | ALREADY_AUTHENTICATED | Reconnect immediately |
| 4007 | INVALID_SEQ | Reconnect and re-identify |
| 4008 | RATE_LIMITED | Wait, then reconnect |
| 4009 | SESSION_TIMED_OUT | Reconnect and re-identify |

## Non-Reconnectable Codes

These codes require user action or indicate permanent failure:

| Code | Name | Action |
|------|------|--------|
| 4004 | AUTHENTICATION_FAILED | Invalid token, re-authenticate |
| 4010 | INVALID_SHARD | Fix shard configuration |
| 4011 | SHARDING_REQUIRED | Implement sharding |
| 4012 | INVALID_API_VERSION | Update client |
| 4013 | INVALID_INTENTS | Fix intents configuration |
| 4014 | DISALLOWED_INTENTS | Request privileged intents |
| 4015 | VERSION_OUTDATED | Update client |

## Special Codes

### 4016 - SERVER_MAINTENANCE

Server is entering maintenance mode.

**Action:**
1. Display maintenance message to user
2. Poll `/status` endpoint every 5 seconds
3. Reconnect when state returns to `running`

### 4017 - SERVER_SHUTDOWN

Server is shutting down.

**Action:**
1. Display shutdown message to user
2. Poll `/status` endpoint every 5 seconds
3. Reconnect when server is back online

## Reconnection Strategy

```
attempt = 0
max_attempts = 10
base_delay = 1000  // 1 second

while attempt < max_attempts:
    delay = min(base_delay * (2 ^ attempt), 60000)  // Max 60 seconds
    wait(delay + random(0, 1000))  // Add jitter
    
    try:
        connect()
        if can_resume:
            send_resume()
        else:
            send_identify()
        break
    except:
        attempt += 1
```

## Rate Limit Handling

If closed with 4008 (RATE_LIMITED):

1. Wait for the retry_after duration
2. Reconnect
3. Reduce message frequency
