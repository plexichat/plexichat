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

- `code` (int/string): Error code (HTTP status or custom)
- `message` (string): Human-readable error message

## HTTP Status Codes

- 200: Success
- 201: Created
- 400: Bad Request - Invalid input
- 401: Unauthorized - Authentication required
- 403: Forbidden - Permission denied
- 404: Not Found - Resource doesn't exist
- 409: Conflict - Resource already exists
- 426: Upgrade Required - Client update needed
- 429: Too Many Requests - Rate limited
- 500: Internal Server Error
- 503: Service Unavailable - Module not available

## Common Error Messages

### Authentication Errors (401)

- Authentication required: No token provided
- Invalid token: Token is malformed or invalid
- Token expired: Token has expired
- Invalid credentials: Wrong username/password
- Invalid code: Wrong 2FA code
- Expired token: Challenge token expired

### Permission Errors (403)

- Access denied: No access to resource
- Permission denied: Missing required permission
- Account locked: Too many failed login attempts
- Admin access required: Admin permission needed
- Cannot message this user: User has blocked you

### Validation Errors (400)

- Invalid input: Request validation failed
- Invalid user ID: ID format invalid
- Invalid channel ID: ID format invalid
- Invalid server ID: ID format invalid
- Invalid message ID: ID format invalid
- Weak password: Password doesn't meet requirements
- Message must have content...: Empty message
- File too large: Upload exceeds size limit
- Invalid file type: Unsupported file format

### Resource Errors (404)

- User not found: User doesn't exist
- Server not found: Server doesn't exist
- Channel not found: Channel doesn't exist
- Message not found: Message doesn't exist
- Webhook not found: Webhook doesn't exist
- Session not found: Session doesn't exist
- Friend request not found: No pending request
- Relationship not found: No relationship exists
- Setting not found: Setting key doesn't exist

### Conflict Errors (409)

- Already exists: Resource already exists
- Username already taken: Username is in use
- Email already taken: Email is in use
- Already a member: Already in server
- 2FA is already enabled: 2FA already active

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

- `VERSION_OUTDATED` (426): Client must update
- `INVALID_VERSION_FORMAT` (400): Malformed version string

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

- retry_after: Seconds to wait before retrying

## Error Handling Best Practices

1. Check HTTP status code first
2. Parse error body for details
3. Handle specific codes appropriately
4. Use exponential backoff for retries
5. Display user-friendly messages

## Example Error Handling

```python
import time

def api_request(url, **kwargs):
    response = requests.request(url, **kwargs)
    
    if response.status_code == 200:
        return response.json()
    
    elif response.status_code == 401:
        # Re-authenticate
        refresh_token()
        return retry_request()
    
    elif response.status_code == 429:
        # Rate limited - wait and retry
        error = response.json().get("error", {})
        retry_after = error.get("retry_after", 1)
        time.sleep(retry_after)
        return retry_request()
    
    elif response.status_code == 426:
        # Update required
        show_update_prompt()
        return None
    
    else:
        error = response.json().get("error", {})
        log_error(error.get("code"), error.get("message"))
        raise APIError(error.get("message"))
```

```javascript
async function apiRequest(url, options) {
    const response = await fetch(url, options);
    
    if (response.ok) {
        return response.json();
    }
    
    const error = await response.json();
    
    if (response.status === 429) {
        const retryAfter = error.error?.retry_after || 1;
        await sleep(retryAfter * 1000);
        return apiRequest(url, options);
    }
    
    throw new Error(error.error?.message || 'Unknown error');
}
```
