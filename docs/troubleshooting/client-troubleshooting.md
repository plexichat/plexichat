# Troubleshoot client issues

This guide helps you diagnose and fix common Plexichat web client issues, including browser debugging, network configuration, and gateway errors.

## Debug in the browser console

### Open Developer Tools

**Chrome or Edge:**

*   Press `F12` or `Ctrl+Shift+I` (Windows) or `Cmd+Option+I` (macOS).
*   Right-click the page and select **Inspect**.

**Firefox:**

*   Press `F12` or `Ctrl+Shift+I` (Windows) or `Cmd+Option+I` (macOS).
*   Right-click the page and select **Inspect Element**.

**Safari:**

*   Enable the Develop menu in **Safari > Preferences > Advanced**.
*   Press `Cmd+Option+I`.

### Review console logs

Look for:

*   Red errors – Critical issues that block functionality.
*   Yellow warnings – Non-critical issues that might affect behavior.
*   Blue info – General information.

**Common errors:**

```
Uncaught ReferenceError: variable is not defined
```

You used a variable or function before defining it. Check for typos or loading order issues.

```
Failed to fetch
```

The network request failed. Check connectivity and CORS settings.

```
WebSocket connection failed
```

The gateway WebSocket failed to connect. Check the gateway URL and firewall rules. See [Gateway connection](../websocket/connection.md).

### Analyze the Network tab

**To analyze failed requests:**

1.  Open the **Network** tab.
2.  Filter by failed status (red).
3.  Click the failed request and review:
    *   Request URL and method
    *   Request headers and payload
    *   Response status, headers, and body

**To analyze a WebSocket connection:**

1.  Filter by `WS` (WebSocket).
2.  Click the gateway connection.
3.  Review sent and received frames.
4.  Check the connection status (`101` means success).

### Profile performance

**To record performance:**

1.  Open the **Performance** tab.
2.  Click **Record**.
3.  Reproduce the slow action.
4.  Click **Stop**.
5.  Review the timeline for long tasks (red bars over 50 ms), script execution, rendering, and network activity.

**To profile memory:**

1.  Open the **Memory** tab.
2.  Select **Heap snapshot** and click **Take snapshot**.
3.  Perform actions.
4.  Take another snapshot and compare to find leaks.

## Fix network and firewall issues

### Check required ports

Plexichat uses these ports:

*   `8000` – API server and WebSocket gateway (`HTTP`/`WS`).
*   `443` – `HTTPS` and secure WebSocket (`WSS`) in production.
*   `8443` – Default client port if you deploy the client separately.

### Configure the firewall

**Windows Firewall:**

```powershell
# Allow outbound connections to the Plexichat server
New-NetFirewallRule -DisplayName "Plexichat" -Direction Outbound -Program "chrome.exe" -RemoteAddress "api.plexichat.com" -Action Allow
```

**Linux (ufw):**

```bash
sudo ufw allow 8000/tcp
sudo ufw allow 443/tcp
```

**Corporate firewalls:** Allow outbound `HTTPS` to your Plexichat server, WebSocket `Upgrade` headers, and long-lived connections without aggressive timeouts.

### Configure a proxy

If you are behind a proxy, configure the browser or client:

**Environment variables:**

```bash
export HTTP_PROXY=http://proxy.example.com:8080
export HTTPS_PROXY=http://proxy.example.com:8080
export NO_PROXY=localhost,127.0.0.1
```

**Browser proxy settings:**

*   Chrome or Edge: **Settings > System > Open proxy settings**.
*   Firefox: **Settings > Network Settings > Manual proxy configuration**.

### Fix TLS and SSL issues

**Self-signed certificates:**

1.  Open the server URL in a browser.
2.  Accept the security warning.
3.  The browser trusts the certificate for that session.

**Certificate errors:** Check that the certificate is not expired, matches the domain, and includes intermediate certificates.

## Review common error codes

### HTTP error codes

| Code | Meaning | Action |
|------|---------|--------|
| `400` | Bad request – Invalid format or missing fields. | Check the request body against the API schema. |
| `401` | Unauthorized – Missing or invalid token. | Re-authenticate and get a new token. |
| `403` | Forbidden – Insufficient permissions or suspended account. | Verify roles or contact an administrator. |
| `404` | Not found – Resource does not exist or URL is incorrect. | Check the endpoint path. |
| `429` | Too many requests – Rate limit exceeded. | Check the `Retry-After` header and use exponential backoff. |
| `500` | Internal server error. | Check server logs or contact an administrator. |
| `502` | Bad gateway – Server temporarily unavailable. | Retry after a delay. |
| `503` | Service unavailable – Maintenance or overload. | Retry later. |

### WebSocket close codes

Close codes are defined in `src/api/websocket/opcodes.py` (`GatewayCloseCode`). Whether you can resume is defined by `RESUMABLE_CLOSE_CODES` and `is_resumable()` in the same file.

| Code | Name | Meaning | Resumable | Action |
|------|------|---------|-----------|--------|
| `1000` | `NORMAL_CLOSURE` | Clean disconnect. | No | No action needed. |
| `1001` | `GOING_AWAY` | Server is going away. | No | Reconnect with a new session. |
| `4000` | `UNKNOWN_ERROR` | Generic internal error. | Yes | Resume with `RESUME` (`op: 6`). If resume fails, send `IDENTIFY`. |
| `4001` | `UNKNOWN_OPCODE` | You sent an unrecognized opcode. | Yes | Fix the opcode and resume. |
| `4002` | `DECODE_ERROR` | Invalid JSON or payload encoding. | Yes | Fix the message format and resume. |
| `4003` | `NOT_AUTHENTICATED` | You sent a message before `IDENTIFY`. | Yes | Send `IDENTIFY` (`op: 2`) or resume. |
| `4004` | `AUTHENTICATION_FAILED` | Token is invalid or rejected. | No | Do not resume. Re-authenticate and send a fresh `IDENTIFY`. |
| `4005` | `ALREADY_AUTHENTICATED` | You already sent `IDENTIFY` on this session. | Yes | Resume instead of re-identifying. |
| `4007` | `INVALID_SEQ` | Resume sequence is invalid. | Yes | Resume with the correct `seq` from the last `DISPATCH`. |
| `4008` | `RATE_LIMITED` | Gateway rate limit exceeded. | Yes | Back off before resuming. Do not reconnect in a tight loop. |
| `4009` | `SESSION_TIMED_OUT` | Session expired while disconnected. | Yes | Resume if the session is still in the resume window; otherwise the server replies with `INVALID_SESSION` (`op: 9`, `d: false`) and you must re-identify. |
| `4010` | `INVALID_SHARD` | Invalid shard requested. | No | Fix the shard configuration. |
| `4011` | `SHARDING_REQUIRED` | Server requires sharding. | No | Implement sharding. |
| `4012` | `INVALID_API_VERSION` | Unsupported API or gateway version. | No | Update the client. |
| `4013` | `INVALID_INTENTS` | Malformed or unsupported `intents` bitmask. See [Gateway intents](../websocket/intents.md). | No | Fix the `intents` value (must be an integer `0`–`4194303`). |
| `4014` | `DISALLOWED_INTENTS` | Privileged intents not permitted. | No | Remove `GUILD_MEMBERS`, `GUILD_PRESENCES`, or `MESSAGE_CONTENT`, or request approval. |
| `4015` | `VERSION_OUTDATED` | Client version is too old. | No | Update the client. |
| `4016` | `SERVER_MAINTENANCE` | Server is entering maintenance mode. | No | Reconnect after maintenance. |
| `4017` | `SERVER_SHUTDOWN` | Server is shutting down intentionally. | No | Reconnect when the server is available. |

**How to handle a close:**

1.  Record the close code, reason, and the last opcode and sequence number.
2.  If the code is resumable, reconnect, wait for `HELLO` (`op: 10`), and send `RESUME` (`op: 6`) with `token`, `session_id`, and `seq`. See [Gateway connection](../websocket/connection.md#resume-a-session).
3.  If the code is not resumable, get a new token if needed and send `IDENTIFY` (`op: 2`) to start a new session.
4.  If the server replies with `INVALID_SESSION` (`op: 9`) and `d: false`, the session is not resumable. Send a fresh `IDENTIFY`.
5.  Always back off when rate-limited (`4008` or `429`).

For the full list and the resumable check, see [Gateway close codes](../websocket/close-codes.md).

## Diagnose common failure modes

### Database connection failures

**Symptoms:** Login fails with a database error, you cannot load servers or messages, or the server returns `500` errors.

**To troubleshoot:**

1.  Check server logs for database errors.
2.  Verify the database is running.
3.  Check the database connection string in your config.
4.  Test connectivity:

    ```bash
    psql -h localhost -U plexichat -d plexichat
    ```

### Valkey connection failures

**Symptoms:** Slow performance, session issues, or caching not working.

**To troubleshoot:**

1.  Check that Valkey is running:

    ```bash
    redis-cli ping
    ```

2.  Verify the Valkey connection in your config.
3.  Check Valkey logs and test connectivity from the server.

### Rate limiting

**Symptoms:** `429` errors, blocked requests, or slow API responses.

**To troubleshoot:**

1.  Check the `Retry-After` header.
2.  Implement exponential backoff.
3.  Reduce request frequency and use caching.

### Authentication failures

**Symptoms:** `401` errors, cannot log in, or token rejected.

**To troubleshoot:**

1.  Verify the token is valid and not expired.
2.  Re-authenticate.
3.  Check two-factor authentication and account status.

### Permission errors

**Symptoms:** `403` errors or access denied messages.

**To troubleshoot:**

1.  Check user roles and permissions.
2.  Verify whether the channel is private.
3.  Contact a server administrator.

## Use debugging techniques

### Enable debug logging

**Client-side:**

```javascript
// Enable verbose logging
localStorage.setItem('plexichat_debug', 'true');

// Check in console
console.log('Debug mode:', localStorage.getItem('plexichat_debug'));
```

**Server-side:**

```yaml
# config/config.yaml
logging:
  level: DEBUG
```

### Capture WebSocket traffic

**In the browser:**

1.  Open the **Network** tab.
2.  Filter by `WS`.
3.  Click the gateway connection.
4.  Open the **Messages** tab and copy frames for analysis.

**Programmatically:**

```javascript
const ws = new WebSocket('ws://localhost:8000/gateway');

ws.addEventListener('message', (event) => {
  console.log('Received:', JSON.parse(event.data));
});
```

### Log API requests

```javascript
// Intercept fetch calls
const originalFetch = window.fetch;
window.fetch = function(...args) {
  console.log('Fetch:', args[0], args[1]);
  return originalFetch.apply(this, args);
};
```

### Analyze stack traces

When an error occurs:

1.  Find the error location.
2.  Look for your code in the stack.
3.  Check the line that caused the error and the variable values at that point.
4.  Check for `null` or `undefined` values.

### Reproduce the issue

1.  Clear browser cache and cookies.
2.  Open the site in incognito or private mode.
3.  Disable browser extensions.
4.  Try a different browser.

*   If the issue disappears in incognito, it is likely a cache or extension issue.
*   If it disappears in a different browser, it is likely browser-specific.
*   If it fails everywhere, it is likely a server or network issue.

## Fix IndexedDB issues

### Clear IndexedDB

**In the browser console:**

```javascript
indexedDB.deleteDatabase('PlexichatCache');
```

**In browser settings:**

*   Chrome: **Settings > Privacy > Clear browsing data > Advanced > IndexedDB**.
*   Firefox: **Options > Privacy & Security > Cookies and Site Data > Manage Data**.

### Handle corruption

**Symptoms:** Messages do not load, cache errors in the console, or infinite loading states.

**To fix:** Clear IndexedDB, reload the page, and re-authenticate. The cache rebuilds from the API.

### Handle quota exceeded

**Symptoms:** `QuotaExceededError`, cannot save messages, or cache failures.

**To fix:** Clear old cache data, reduce cache size, implement cache eviction, or compress cached data.

## Fix performance issues

### Slow message loading

**Causes:** Too many cached messages, large history, or slow API responses.

**To fix:** Implement pagination, limit cache size, lazy-load messages, or use virtual scrolling.

### High memory usage

**Causes:** Memory leaks, large cached data, or unclosed connections.

**To fix:** Profile memory, clear the cache periodically, close unused connections, and allow garbage collection.

### Slow UI rendering

**Causes:** Too many DOM elements, inefficient rendering, or large message lists.

**To fix:** Use virtual scrolling, implement lazy rendering, debounce scroll events, and optimize CSS selectors.

## Get help

If you cannot resolve an issue:

1.  **Collect information:** Browser and version, operating system, console errors, Network tab screenshots, and steps to reproduce.
2.  **Check documentation:** [API Reference](../api/index.md), [WebSocket gateway](../websocket/README.md), and [Deployment guide](../deployment/index.md).
3.  **Contact support:** Your server administrator for server issues, community forums for client issues, or GitHub issues for bug reports.
4.  **Provide context:** What you tried to do, what happened, what you expected, and any error messages or screenshots.
