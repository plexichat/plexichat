# Client-Side Troubleshooting

This guide covers common issues and debugging techniques for Plexichat web clients.

## Browser Console Debugging

### Opening Developer Tools

**Chrome/Edge:**
- Press F12 or Ctrl+Shift+I (Windows), Cmd+Option+I (Mac)
- Right-click page -> Inspect

**Firefox:**
- Press F12 or Ctrl+Shift+I (Windows), Cmd+Option+I (Mac)
- Right-click page -> Inspect Element

**Safari:**
- Enable Develop menu in Safari > Preferences > Advanced
- Press Cmd+Option+I

### Console Logs

The console shows JavaScript errors and warnings. Look for:

- Red errors: Critical issues preventing functionality
- Yellow warnings: Non-critical issues that may affect behavior
- Blue info: General information

**Common Console Errors:**

```
Uncaught ReferenceError: variable is not defined
```
A variable or function was used before being defined. Check for typos or loading order issues.

```
Failed to fetch
```
Network request failed. Check network connectivity and CORS settings.

```
WebSocket connection failed
```
WebSocket could not connect. Check gateway URL and firewall rules.

### Network Tab Analysis

The Network tab shows all HTTP requests and WebSocket connections.

**Analyzing Failed Requests:**
1. Open Network tab
2. Filter by "Failed" status (red)
3. Click failed request to see details:
   - Request URL and method
   - Request headers
   - Request payload
   - Response status and headers
   - Response body

**WebSocket Connection Analysis:**
1. Filter by "WS" (WebSocket)
2. Click the gateway connection
3. View frames sent and received
4. Check connection status (101 = successful, others = failed)

### Performance Profiling

**Recording Performance:**
1. Open Performance tab
2. Click Record
3. Perform the action causing slowness
4. Click Stop
5. Analyze the timeline:
   - Long tasks (red bars > 50ms)
   - Script execution time
   - Rendering and painting
   - Network activity

**Memory Profiling:**
1. Open Memory tab
2. Select "Heap snapshot"
3. Click Take snapshot
4. Perform actions
5. Take another snapshot
6. Compare snapshots to find memory leaks

## Network and Firewall Issues

### Required Ports

Plexichat requires the following ports:

- **8000**: API server and WebSocket gateway (HTTP/WS)
- **443**: HTTPS and secure WebSocket (WSS) in production
- **8443**: Default client port (if using separate client deployment)

### Firewall Configuration

**Windows Firewall:**
```powershell
# Allow outbound connections to Plexichat server
New-NetFirewallRule -DisplayName "Plexichat" -Direction Outbound -Program "chrome.exe" -RemoteAddress "api.plexichat.com" -Action Allow
```

**Linux (ufw):**
```bash
sudo ufw allow 8000/tcp
sudo ufw allow 443/tcp
```

**Corporate Firewalls:**
Ensure your network allows:
- Outbound HTTPS to your Plexichat server
- WebSocket connections (Upgrade header)
- Long-lived connections (no aggressive timeout)

### Proxy Configuration

If behind a proxy, configure your browser or client:

**Environment Variables:**
```bash
export HTTP_PROXY=http://proxy.example.com:8080
export HTTPS_PROXY=http://proxy.example.com:8080
export NO_PROXY=localhost,127.0.0.1
```

**Browser Proxy Settings:**
- Chrome/Edge: Settings > System > Open proxy settings
- Firefox: Settings > Network Settings > Manual proxy configuration

### TLS/SSL Issues

**Self-Signed Certificates:**
If using a self-signed certificate:
1. Open the server URL in a browser
2. Accept the security warning
3. The certificate will be trusted for that session

**Certificate Errors:**
- Check certificate validity (not expired)
- Verify certificate matches domain name
- Ensure intermediate certificates are installed

## Common Error Codes

### HTTP Error Codes

**400 Bad Request**
- Invalid request format
- Missing required fields
- Check request body against API schema

**401 Unauthorized**
- Missing or invalid token
- Token expired
- Re-authenticate and obtain new token

**403 Forbidden**
- Insufficient permissions
- Account suspended
- Contact server administrator

**404 Not Found**
- Resource does not exist
- Incorrect URL
- Check endpoint path

**429 Too Many Requests**
- Rate limit exceeded
- Check Retry-After header
- Implement exponential backoff

**500 Internal Server Error**
- Server error
- Check server logs
- Contact server administrator

**502 Bad Gateway**
- Server temporarily unavailable
- Retry after delay
- Check server status

**503 Service Unavailable**
- Server maintenance or overload
- Check status page
- Retry later

### WebSocket Close Codes

**1000 Normal Closure**
- Clean disconnect
- Expected behavior

**1001 Going Away**
- Server shutting down
- Reconnect after delay

**4000 Unknown Error**
- Unexpected server error
- Resumable with op:6 Resume

**4001 Unknown Opcode**
- Invalid opcode sent
- Check client implementation

**4002 Decode Error**
- Invalid JSON or encoding
- Check message format

**4003 Not Authenticated**
- Sent message before IDENTIFY
- Send IDENTIFY first

**4004 Authentication Failed**
- Invalid token
- Re-authenticate

**4005 Already Authenticated**
- Already sent IDENTIFY
- Resume instead

**4007 Invalid Seq**
- Invalid sequence number
- Use correct sequence from events

**4008 Rate Limited**
- Too many messages
- Slow down message rate

**4009 Session Timed Out**
- Missed heartbeats
- Improve heartbeat handling

**4010 Invalid Shard**
- Invalid sharding info
- Check shard configuration

**4011 Sharding Required**
- Server requires sharding
- Implement sharding

**4012 Invalid API Version**
- Unsupported API version
- Update client

**4013 Invalid Intents**
- Invalid intent value
- Check intent calculation

**4014 Disallowed Intents**
- Privileged intent not allowed
- Remove privileged intents or get approval

**4015 Version Outdated**
- Client version too old
- Update client

**4016 Server Maintenance**
- Server entering maintenance
- Reconnect after maintenance

**4017 Server Shutdown**
- Server is shutting down
- Reconnect when server back online

## Common Failure Modes

### Database Connection Failures

**Symptoms:**
- Login fails with database error
- Cannot load servers or messages
- Server returns 500 errors

**Troubleshooting:**
1. Check server logs for database errors
2. Verify database is running
3. Check database connection string in config
4. Test database connectivity:
   ```bash
   psql -h localhost -U plexichat -d plexichat
   ```

### Redis Connection Failures

**Symptoms:**
- Slow performance
- Session issues
- Caching not working

**Troubleshooting:**
1. Check Redis is running:
   ```bash
   redis-cli ping
   ```
2. Verify Redis connection in config
3. Check Redis logs
4. Test Redis connectivity from server

### Rate Limiting

**Symptoms:**
- 429 errors
- Requests blocked
- Slow API responses

**Troubleshooting:**
1. Check Retry-After header
2. Implement exponential backoff
3. Reduce request frequency
4. Use caching to reduce API calls

### Authentication Failures

**Symptoms:**
- 401 errors
- Cannot login
- Token rejected

**Troubleshooting:**
1. Verify token is valid
2. Check token expiration
3. Re-authenticate
4. Verify 2FA if enabled
5. Check account status (not suspended)

### Permission Errors

**Symptoms:**
- 403 errors
- Cannot perform actions
- Access denied messages

**Troubleshooting:**
1. Check user roles
2. Verify role permissions
3. Contact server administrator
4. Check if channel is private

## Debugging Techniques

### Enabling Debug Logging

**Client-Side:**
```javascript
// Enable verbose logging
localStorage.setItem('plexichat_debug', 'true');

// Check in console
console.log('Debug mode:', localStorage.getItem('plexichat_debug'));
```

**Server-Side:**
```yaml
# config/config.yaml
logging:
  level: DEBUG
```

### Capturing WebSocket Traffic

**Browser:**
1. Open Network tab
2. Filter by WS
3. Click gateway connection
4. View Messages tab
5. Copy frames for analysis

**Programmatic:**
```javascript
const ws = new WebSocket('ws://localhost:8000/gateway');

ws.addEventListener('message', (event) => {
  console.log('Received:', JSON.parse(event.data));
});

ws.addEventListener('send', (event) => {
  console.log('Sent:', event.data);
});
```

### API Request Logging

```javascript
// Intercept fetch calls
const originalFetch = window.fetch;
window.fetch = function(...args) {
  console.log('Fetch:', args[0], args[1]);
  return originalFetch.apply(this, args);
};
```

### Stack Trace Analysis

When an error occurs, examine the stack trace:

1. Identify the error location
2. Look for your code in the stack
3. Check the line causing the error
4. Verify variable values at that point
5. Check for null/undefined values

### Reproducing Issues

**Steps to Reproduce:**
1. Clear browser cache and cookies
2. Open in incognito/private mode
3. Disable browser extensions
4. Try different browser
5. Check if issue is reproducible

**Isolating the Issue:**
- If works in incognito: Cache or extension issue
- If works in different browser: Browser-specific issue
- If fails everywhere: Server or network issue

## IndexedDB Issues

### Clearing IndexedDB

**Browser Console:**
```javascript
indexedDB.deleteDatabase('PlexichatCache');
```

**Browser Settings:**
- Chrome: Settings > Privacy > Clear browsing data > Advanced > IndexedDB
- Firefox: Options > Privacy & Security > Cookies and Site Data > Manage Data

### IndexedDB Corruption

**Symptoms:**
- Messages not loading
- Cache errors in console
- Infinite loading states

**Troubleshooting:**
1. Clear IndexedDB
2. Reload page
3. Re-authenticate
4. Cache will rebuild from API

### IndexedDB Quota Exceeded

**Symptoms:**
- QuotaExceededError
- Cannot save messages
- Cache failures

**Troubleshooting:**
1. Clear old cache data
2. Reduce cache size
3. Implement cache eviction policy
4. Use compression for cached data

## Performance Issues

### Slow Message Loading

**Causes:**
- Too many messages in cache
- Large message history
- Slow API response

**Solutions:**
1. Implement pagination
2. Limit cache size
3. Lazy load messages
4. Use virtual scrolling

### High Memory Usage

**Causes:**
- Memory leaks
- Large cached data
- Unclosed connections

**Solutions:**
1. Profile memory usage
2. Clear cache periodically
3. Close unused connections
4. Implement garbage collection

### Slow UI Rendering

**Causes:**
- Too many DOM elements
- Inefficient rendering
- Large message lists

**Solutions:**
1. Use virtual scrolling
2. Implement lazy rendering
3. Debounce scroll events
4. Optimize CSS selectors

## Getting Help

If you cannot resolve an issue:

1. **Collect Information:**
   - Browser and version
   - Operating system
   - Error messages from console
   - Network tab screenshots
   - Steps to reproduce

2. **Check Documentation:**
   - [API Reference](../api/index.md)
   - [WebSocket Guide](../client-development/websocket.md)
   - [Deployment Guide](../deployment/index.md)

3. **Contact Support:**
   - Server administrator for server issues
   - Community forums for client issues
   - GitHub issues for bug reports

4. **Provide Context:**
   - What you were trying to do
   - What happened instead
   - What you expected to happen
   - Any error messages or screenshots
