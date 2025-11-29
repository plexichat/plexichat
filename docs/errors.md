# Error Handling

All API errors follow a consistent format.

## Error Response Format

```json
{
  "error": {
    "code": 404,
    "message": "Resource not found"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| code | int/string | Error code |
| message | string | Human-readable message |

## HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request - Invalid input |
| 401 | Unauthorized - Authentication required |
| 403 | Forbidden - Permission denied |
| 404 | Not Found - Resource doesn't exist |
| 409 | Conflict - Resource already exists |
| 426 | Upgrade Required - Client update needed |
| 429 | Too Many Requests - Rate limited |
| 500 | Internal Server Error |

## Common Error Codes

### Authentication Errors (401)

| Message | Description |
|---------|-------------|
| Authentication required | No token provided |
| Invalid token | Token is malformed or invalid |
| Token expired | Token has expired |
| Token revoked | Token was revoked |

### Permission Errors (403)

| Message | Description |
|---------|-------------|
| Access denied | No access to resource |
| Permission denied | Missing required permission |
| Account locked | Too many failed attempts |
| Email not verified | Email verification required |

### Validation Errors (400)

| Message | Description |
|---------|-------------|
| Invalid input | Request validation failed |
| Invalid user ID | ID format invalid |
| Invalid channel ID | ID format invalid |
| Weak password | Password doesn't meet requirements |
| Empty message | Message has no content |

### Resource Errors (404)

| Message | Description |
|---------|-------------|
| User not found | User doesn't exist |
| Server not found | Server doesn't exist |
| Channel not found | Channel doesn't exist |
| Message not found | Message doesn't exist |

## Version Errors

Version-related errors include additional fields:

```json
{
  "error": {
    "code": "VERSION_OUTDATED",
    "message": "Client version a.0.9-1 is no longer supported",
    "client_version": "a.0.9-1",
    "min_version": "a.1.0-1",
    "server_version": "a.1.0-1",
    "update_url": "https://..."
  }
}
```

### Version Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| VERSION_OUTDATED | 426 | Client must update |
| INVALID_VERSION_FORMAT | 400 | Malformed version string |

## Rate Limit Errors (429)

```json
{
  "error": {
    "code": 429,
    "message": "Rate limited",
    "retry_after": 1.5
  }
}
```

| Field | Description |
|-------|-------------|
| retry_after | Seconds to wait before retrying |

## Conflict Errors (409)

| Message | Description |
|---------|-------------|
| Already exists | Resource already exists |
| Username taken | Username is in use |
| Email taken | Email is in use |
| Already friends | Already in relationship |
| Already blocked | User already blocked |

## Error Handling Best Practices

1. **Check status code first** - Determine error category
2. **Parse error body** - Get detailed error information
3. **Handle specific codes** - Implement specific handlers
4. **Log errors** - Record for debugging
5. **User feedback** - Display appropriate messages

## Example Error Handling

```python
response = api.request(...)

if response.status_code == 200:
    return response.json()
elif response.status_code == 401:
    # Re-authenticate
    refresh_token()
elif response.status_code == 429:
    # Rate limited
    retry_after = response.json()["error"]["retry_after"]
    time.sleep(retry_after)
    return retry_request()
elif response.status_code == 426:
    # Update required
    show_update_prompt()
else:
    error = response.json()["error"]
    log_error(error["code"], error["message"])
```
