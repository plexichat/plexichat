# Admin API Routes

Administrative API endpoints under `/api/v1/admin/` for server management, moderation, and system configuration. All endpoints require elevated permissions (admin role + 2FA).

## Route Files

### `auth.py`
Admin authentication and session management:
- Admin login with 2FA verification
- Session lifecycle management
- Password and token validation

### `users.py`
User administration:
- List, search, and filter users
- Create, edit, and suspend user accounts
- Role and permission assignment
- Account deletion and data export

### `moderation.py`
Content and user moderation:
- Warning and timeout management
- Ban/unban operations
- Moderation history and appeals
- Bulk moderation actions

### `dashboard.py`
Admin dashboard and system overview:
- Server statistics and metrics
- Active users, message counts, storage usage
- System health indicators
- Recent activity feed

### `audit.py`
Audit logging:
- Search and filter audit log entries
- Export audit logs (CSV, JSON)
- Retention policy management
- Compliance reporting

### `approvals.py`
Approval workflows:
- Pending approval queue
- Approve/reject actions
- Escalation rules

### `bots.py`
Bot management:
- List registered bots
- Create, update, delete bot accounts
- Bot permission scoping
- Rate limit configuration

### `database.py`
Database administration:
- Manual backup/restore triggers
- Database optimization (VACUUM, ANALYZE)
- Storage statistics
- Replication status (PostgreSQL)

### `licensing.py`
License management:
- License key validation and activation
- License info and expiry tracking
- Feature flag management

### `logs.py`
System log viewer:
- Log level filtering (debug, info, warn, error)
- Time range and source filtering
- Log export and rotation management

### `migrations.py`
Database migration management:
- View migration status and history
- Run pending migrations
- Rollback migrations
- Integrity validation

### `plexijoin.py`
Federation administration:
- Manage federation connections
- View federation status
- Approve/reject federation requests

### `reindex.py`
Search index management:
- Trigger full reindex
- View index status and statistics
- Schedule maintenance indexing

### `roles.py`
Role and permission management:
- Create/edit/delete roles
- Configure permission sets
- Role assignment to users
- Permission inheritance

### `security.py`
Security configuration:
- Password policy settings
- 2FA enforcement levels
- IP blocking and rate limiting
- Session timeout configuration

### `telemetry.py`
Telemetry management:
- View collected telemetry data
- Configure telemetry collection
- Data retention settings
- Export telemetry reports

### `tickets.py`
Support ticket system:
- View and manage support tickets
- Assign tickets to admins
- Ticket status and priority management

### `ui.py`
Admin UI customization:
- Branding and theming options
- Custom announcements
- Login page customization
- Footer and legal links

### `utils.py`
Utility endpoints:
- Health check endpoints
- Cache invalidation
- System information
- Maintenance mode toggle

## Authentication

All admin routes require:
1. Valid JWT token with admin scope
2. Completed 2FA verification for write operations
3. IP whitelist check (configurable)
4. Audit logging for all state-changing operations

## Rate Limiting

- Read operations: 100 req/min
- Write operations: 30 req/min
- Bulk operations: 5 req/min
