# Admin Panel User Guide

The Plexichat Admin Panel provides a web-based interface for server operators to manage users, monitor system health, configure security settings, and oversee server operations.

## Accessing the Admin Panel

### Default URL

```
http://localhost:8000/api/v1/admin/ui
```

For production, replace with your server's admin panel URL (e.g., `https://chat.example.com/api/v1/admin/ui`).

### Authentication

Access to the admin panel requires:

1. **Operator Status**: Your user account must have `is_operator: true` in the database
2. **Two-Factor Authentication**: If `admin_ui.require_otp` is enabled in config, you must have 2FA enabled on your account

### First-Time Setup

To grant yourself operator access:

```bash
# Connect to your database
psql -U plexichat -d plexichat

# Grant operator status
UPDATE users SET is_operator = true WHERE username = 'your_username';
```

## Dashboard Overview

The admin panel dashboard provides a quick overview of your Plexichat instance:

### Key Metrics

- **Total Users**: Number of registered user accounts
- **Active Servers**: Total servers (guilds) created
- **Online Users**: Currently connected users
- **System Health**: Database, Redis, and API status
- **Storage Usage**: Media and attachment storage consumption

### Recent Activity

- New user registrations
- Server creations
- Reported content (if AutoMod is enabled)
- System alerts and warnings

## User Management

### Viewing Users

Navigate to **Users** in the sidebar to see all registered users. Features include:

- Search by username, email, or user ID
- Filter by status (active, banned, pending deletion)
- Sort by registration date, last activity, or message count
- Export user list to CSV

### Managing Individual Users

Click on a user to view their profile and manage:

#### Profile Actions

- **View Profile**: See user's public info, servers, and activity
- **Edit Badges**: Add or remove user badges (e.g., `early_supporter`, `verified`)
- **Change Tier**: Modify user tier (affects rate limits and features)
- **Force Username Change**: Require user to change their username on next login
- **Disable Account**: Temporarily disable account (user can re-enable)
- **Schedule Deletion**: Initiate 30-day account deletion grace period
- **Ban Account**: Permanently ban with optional reason

#### User Details Panel

- Registration date and IP
- Last login time and IP
- 2FA status
- Email verification status
- Associated servers (as member or owner)
- Direct message channels
- Stored settings and preferences

### Bulk Operations

Select multiple users to perform bulk actions:

- Export user data (GDPR compliance)
- Send mass email (if email is configured)
- Apply badges to multiple users
- Ban multiple accounts

## Server Management

### Viewing Servers

Navigate to **Servers** to see all servers on your instance:

- Search by server name or ID
- Filter by member count, creation date, or owner
- View server statistics

### Server Actions

Click on a server to manage:

- **View Server**: See channels, members, and settings
- **Transfer Ownership**: Transfer server to another user
- **Delete Server**: Permanently delete server and all data
- **View Audit Log**: See all administrative actions in the server

### Server Discovery

Enable or disable server discovery features:
- Public server directory listing
- Server categories and tags
- Discovery eligibility requirements

## Security & Moderation

### Access Control

#### IP Blocking

Navigate to **Security > IP Blocks** to:

- Block specific IP addresses or CIDR ranges
- View blocked IPs and block history
- Set expiration times for temporary blocks
- Import blocklists from files

#### Banned Usernames

Navigate to **Security > Banned Usernames** to:

- Add username patterns that cannot be registered
- Use wildcards (e.g., `admin*`, `*support*`)
- View registration attempts with banned names

### AutoMod Configuration

Navigate to **AutoMod** to configure automated moderation:

#### Content Filters

- **Spam Detection**: Message rate limits and duplicate content
- **Word Filters**: Banned words and phrases with severity levels
- **Link Filtering**: Allowed/blocked domains
- **Mention Limits**: Max mentions per message
- **Attachment Restrictions**: File type and size limits

#### Actions

Configure automated responses:
- Delete message
- Timeout user (temporary mute)
- Kick from server
- Ban from server
- Flag for review
- Send alert to moderators

#### Machine Learning

If enabled, configure ML-based content classification:
- Toxicity thresholds
- Sentiment analysis
- Image content classification

### Access Tokens

Some Plexichat deployments require an additional access token for authenticated REST API requests. This provides defense-in-depth: even if a session token is leaked, the attacker cannot make API requests without the separate access token.

#### When Access Tokens Apply

When access-token gating is enabled, every authenticated API request requires **two** credentials:

1. **Session or bot authorization**: `Authorization: Bearer <session-token>` or `Authorization: Bot <bot-token>`
2. **Access token**: `X-API-Access-Token: <access-token>`

#### Detecting the Requirement

Clients can discover whether access-token gating is active without making authenticated requests:

```bash
curl https://api.plexichat.com/api/v1/capabilities
```

The response includes an `access_token_required` field. If `true`, the `X-API-Access-Token` header must be included on all authenticated requests.

**Client Implementation:**

- Check `GET /capabilities` on startup and when configuration changes
- If `access_token_required: true`, prompt the user for the access token or read it from configuration
- If `access_token_required: false`, omit the header — it is not required and will be ignored
- Cache the capability check result, but re-check periodically (e.g., on reconnect or hourly)

#### Request Shape

With access-token gating enabled:

```http
GET /api/v1/users/@me HTTP/1.1
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
X-API-Access-Token: plexi_at_a1b2c3d4e5f6...
```

#### Use Cases

- **Closed deployments**: Private instances where only authorized users should access the API
- **Staged testing**: Pre-release environments limiting API access to testers
- **Regulatory compliance**: Environments requiring multiple authentication factors
- **Temporary lockdown**: Quickly restrict API access during a security incident without revoking all sessions

#### Security Practices

**Treat access tokens as sensitive credentials:**

- Never hardcode access tokens into public client applications
- Store access tokens in secure configuration (environment variables, secure storage)
- Support rotation — access tokens should be changeable without client code changes
- Support revocation — if compromised, revoke immediately

**Access token vs session token:**

| Aspect | Session Token | Access Token |
|--------|---------------|--------------|
| Scope | Single user session | Entire API |
| Lifetime | Hours to days | Until rotated by admin |
| Source | Generated on login | Admin-configured |
| Header | `Authorization: Bearer` | `X-API-Access-Token` |
| Per-user | Yes (one per session) | No (shared across users) |

#### Admin Panel Management

Navigate to **Security > Access Tokens** to:

- View all active access tokens
- Revoke suspicious tokens
- Configure token policies (max age, rotation requirements)
- Generate service account tokens
- Enable/disable access token gating globally

#### Error Handling

When the access token gate is active and a request is missing or has an invalid access token:

- **Status**: `403 Forbidden` (not 401 — the user is authenticated, but access is denied)
- **Error code**: `ACCESS_TOKEN_REQUIRED` or `INVALID_ACCESS_TOKEN`
- **Message**: Indicates that an access token is required

Clients should handle this differently from a 401 (authentication failure) — a 401 means the session is invalid and the user needs to re-login, while a 403 from the access token gate means the access token needs to be configured.

## Audit Logs

### System Audit Log

Navigate to **Audit Log** to view all administrative actions:

- User management actions (bans, edits, deletions)
- Server management (deletions, ownership transfers)
- Security actions (IP blocks, token revocations)
- Configuration changes
- Failed admin authentication attempts

Filter by:
- Date range
- Admin user
- Action type
- Target user/server

### Exporting Logs

Export audit logs for compliance:
- CSV format for spreadsheet analysis
- JSON format for programmatic processing
- Filtered exports based on criteria

## Database & System Health

### Database Status

Navigate to **System > Database** to view:

- Connection pool status
- Query performance metrics
- Table sizes and row counts
- Recent slow queries
- Migration status

### Redis Cache

Navigate to **System > Cache** to view:

- Cache hit/miss rates
- Memory usage
- Key expiration statistics
- Connected clients

### API Performance

Navigate to **System > API** to view:

- Request rates by endpoint
- Response time percentiles (p50, p95, p99)
- Error rates
- Rate limiting statistics
- WebSocket connection metrics

### Maintenance Tasks

Perform maintenance operations:

- **Clear Cache**: Flush Redis cache
- **Rebuild Search Index**: Reindex messages for search
- **Clean Orphaned Files**: Remove unreferenced attachments
- **Optimize Database**: Run VACUUM and ANALYZE
- **Backup Now**: Trigger immediate database backup

## Telemetry & Analytics

### System Telemetry

Navigate to **Telemetry** to view:

- Daily/monthly active users
- Message volume trends
- Server growth
- Feature usage statistics
- Performance trends over time

### Custom Reports

Build custom reports with:
- Date range selection
- Metric selection
- Grouping options (by day, week, month)
- Export to CSV or JSON

## Configuration

### Viewing Current Config

Navigate to **Configuration** to view current server configuration:

- Read-only view of all config values
- Environment variable resolution
- Default vs. custom values highlighted

### Runtime Config Updates

Some configuration values can be updated without restart:

- Rate limiting thresholds
- Feature flags
- Logging levels
- Maintenance mode

**Note**: Core configuration changes (database, security keys) require server restart.

## Tickets & Support

### User Tickets

If ticketing is enabled, navigate to **Tickets** to:

- View open and closed tickets
- Assign tickets to staff
- Respond to users
- Escalate to higher tiers
- Track resolution times

### Support Settings

Configure ticket system:
- Enable/disable ticketing
- Auto-assignment rules
- Response time SLAs
- Email notifications

## Admin Panel Settings

### UI Preferences

Customize your admin panel experience:

- Theme (light/dark)
- Default page on login
- Notification preferences
- Dashboard widget arrangement

### Security Settings

Configure admin panel security:

- Session timeout
- Require 2FA for all admins
- IP whitelist for admin access
- Admin action approval workflows

## Best Practices

### Security

1. **Enable 2FA**: Require 2FA for all operator accounts
2. **Strong Passwords**: Enforce strong passwords for admin accounts
3. **IP Whitelisting**: Restrict admin panel to specific IP ranges in production
4. **Audit Logging**: Regularly review audit logs for suspicious activity
5. **Principle of Least Privilege**: Only grant operator status to trusted users

### Performance

1. **Regular Maintenance**: Schedule weekly cache clears and database optimization
2. **Monitor Metrics**: Set up alerts for high error rates or slow queries
3. **Backup Strategy**: Verify automated backups are running
4. **Resource Planning**: Monitor growth trends for capacity planning

### User Privacy

1. **GDPR Compliance**: Use data export tools for user requests
2. **Data Retention**: Configure automatic deletion of old audit logs
3. **Access Logs**: Regularly review who accessed user data
4. **Transparency**: Document what admin actions are logged

## Troubleshooting

### Cannot Access Admin Panel

1. Verify you're accessing the correct URL
2. Check that your account has `is_operator = true`
3. Check server logs for authentication errors
4. Verify 2FA is enabled if required

### Changes Not Saving

1. Check that config file is writable
2. Verify database connection
3. Check Redis connection for cached settings
4. Review error logs for details

### Admin Panel Slow

1. Check database performance metrics
2. Verify Redis is responding quickly
3. Consider increasing connection pool sizes
4. Review network latency to server

## API Reference

The admin panel uses these API endpoints internally:

- `GET /api/v1/admin/dashboard` - Dashboard metrics
- `GET /api/v1/admin/users` - User list
- `GET /api/v1/admin/users/{id}` - User details
- `PATCH /api/v1/admin/users/{id}` - Update user
- `DELETE /api/v1/admin/users/{id}` - Delete user
- `GET /api/v1/admin/servers` - Server list
- `DELETE /api/v1/admin/servers/{id}` - Delete server
- `GET /api/v1/admin/audit-logs` - Audit log entries
- `GET /api/v1/admin/health` - System health
- `GET /api/v1/admin/database/status` - Database status

See [Admin API Reference](../api/admin.md) for detailed endpoint documentation.

## Getting Help

- **Documentation**: [Plexichat Documentation](../README.md)
- **Configuration**: [Configuration Guide](../configuration.md)
- **Deployment**: [Deployment Guide](../deployment.md)
- **API Reference**: [Admin API](../api/admin.md)
