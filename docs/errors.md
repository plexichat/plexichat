# Error handling

All API errors use a single JSON envelope. Check the HTTP status code first, then parse the body for details.

## Error envelope

Every error response returns this shape (`src/api/schemas/common.py:58`):

```json
{
  "error": {
    "code": 400,
    "message": "Request validation failed"
  }
}
```

| Field | Type | Description |
| --- | --- | --- |
| `error.code` | integer | HTTP status code that matches the response status. |
| `error.message` | string | Human-readable message. For 5xx errors this is a generic safe message; for 4xx errors it is client-actionable. |
| `error.retry_after` | number | Included only for 429 rate-limit errors. Seconds to wait before retrying. |
| `error.traceback` | string | Included only for self-test requests with `X-Plexichat-SelfTest-Debug: true` when `selftest.capture_stack_traces` is enabled. |

The server never leaks SQL, file paths, or internal details in `error.message`. Messages are sanitized server-side (`src/api/middleware/error_handling.py:71`).

### Validation errors are 400, not 422

FastAPI generates 422 for Pydantic validation failures by default. Plexichat normalizes all `RequestValidationError` responses to `400` with the standard envelope (`src/api/middleware/error_handling.py:245` and `src/api/middleware/error_handling.py:372`). If you handle validation failures, check for `400` — your client will never receive `422` from this API.

Example validation error:

```json
{
  "error": {
    "code": 400,
    "message": "Invalid size: value is not a valid integer"
  }
}
```

## HTTP status codes

| Code | Meaning | When it occurs |
| --- | --- | --- |
| 200 | OK | Request succeeded. |
| 201 | Created | Resource created. |
| 400 | Bad request | Validation failed, malformed ID, weak password, empty message, malformed `Content-Length`, or unknown version format. |
| 401 | Unauthorized | Missing, invalid, or expired token or credentials. |
| 403 | Forbidden | Permission denied, blocked user, account locked, or missing license feature. |
| 404 | Not found | Resource does not exist. |
| 409 | Conflict | Resource already exists, username or email taken, or duplicate membership. |
| 410 | Gone | Resource is archived or no longer available. |
| 423 | Locked | Resource is locked. |
| 426 | Upgrade required | Client version is no longer supported (`VERSION_OUTDATED`). |
| 429 | Too many requests | Rate limited. Check `retry_after`. |
| 500 | Internal server error | Generic server error. `message` is always `Internal server error` when `api.debug` is false. |
| 503 | Service unavailable | Module not available. |
| 507 | Insufficient storage | Channel is full. |

## Error details by category

### Authentication errors (401)

- `Authentication required` — No token provided.
- `Invalid token` — Token is malformed or invalid.
- `Token expired` — Token has expired.
- `Invalid credentials` — Wrong username or password.
- `Invalid code` — Wrong 2FA code.
- `Expired token` — Challenge token expired.

### Permission errors (403)

- `Access denied` — You don't have access to the resource.
- `Permission denied` — You lack the required permission.
- `Account locked` — Too many failed login attempts.
- `Admin access required` — Admin permission is required.
- `Cannot message this user` — The user has blocked you.

### Validation errors (400)

Validation errors return `400` with the envelope above, never `422`.

- `Request validation failed` — Generic validation failure (first field error is surfaced as `Invalid <field>: <reason>`).
- `Invalid user ID` / `Invalid channel ID` / `Invalid server ID` / `Invalid message ID` — ID format is invalid.
- `Weak password` — Password does not meet policy.
- `Message must have content...` — Empty message.
- `File too large` — Upload exceeds the size limit.
- `Invalid file type` — Unsupported file format.
- `Invalid storage URL` — Voice metadata URL failed private-network or credential checks (`src/api/routes/features/voice.py:40`).
- `Invalid version format` — Malformed version string (`INVALID_VERSION_FORMAT`).

### Resource errors (404)

- `User not found` / `Server not found` / `Channel not found` / `Message not found`
- `Webhook not found` / `Session not found` / `Friend request not found`
- `Relationship not found` / `Setting not found`

### Conflict errors (409)

- `Already exists` — Resource already exists.
- `Username already taken` / `Email already taken`
- `Already a member` — Already in the server.
- `2FA is already enabled`

## Version errors

Version errors include extra fields for client update logic:

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

| Code | HTTP status | Meaning |
| --- | --- | --- |
| `VERSION_OUTDATED` | 426 | Update your client. |
| `INVALID_VERSION_FORMAT` | 400 | Version string is malformed. |

## Rate-limit errors (429)

```json
{
  "error": {
    "code": 429,
    "message": "Rate limited",
    "retry_after": 1.5
  }
}
```

`retry_after` is the number of seconds to wait before retrying. Use exponential backoff and respect this value.

## How to handle errors

1. Check the HTTP status code.
2. Parse `error.code` and `error.message`. For 429, read `error.retry_after`.
3. For 400, show the message to the user — it already names the invalid field.
4. For 401, refresh or re-authenticate.
5. For 426, prompt the user to update.

### Python example

```python
import time
import requests

def api_request(url, **kwargs):
    response = requests.request(url, **kwargs)

    if response.ok:
        return response.json()

    body = response.json()
    error = body.get("error", {})

    if response.status_code == 401:
        refresh_token()
        return retry_request()

    if response.status_code == 429:
        retry_after = error.get("retry_after", 1)
        time.sleep(retry_after)
        return retry_request()

    if response.status_code == 426:
        show_update_prompt()
        return None

    if response.status_code == 400:
        # Validation error — message already contains the field name
        raise ValueError(error.get("message", "Invalid request"))

    log_error(error.get("code"), error.get("message"))
    raise APIError(error.get("message", "Request failed"))
```

### JavaScript example

```javascript
async function apiRequest(url, options) {
  const response = await fetch(url, options);

  if (response.ok) {
    return response.json();
  }

  const body = await response.json();
  const error = body.error || {};

  if (response.status === 429) {
    const retryAfter = error.retry_after || 1;
    await sleep(retryAfter * 1000);
    return apiRequest(url, options);
  }

  if (response.status === 400) {
    throw new Error(error.message || 'Invalid request');
  }

  throw new Error(error.message || 'Unknown error');
}
```

## Related documentation

- `src/api/middleware/error_handling.py:109` — `format_error_response` envelope
- `src/api/middleware/error_handling.py:306` — `setup_exception_handlers` handlers
- `src/api/schemas/common.py:49` — `ErrorDetail` and `ErrorResponse` models
