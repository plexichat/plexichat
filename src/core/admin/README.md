# Admin Module

Administrative functions for Plexichat server management.

## Overview

This module provides comprehensive administrative capabilities including:
- Admin user management with role-based access control
- Database migration management
- System metrics and monitoring
- Security and access control
- Feedback/ticket viewing and management
- User management and moderation tools
- AutoMod configuration
- System logs viewing
- Role and approval management

## Features

### Dashboard Overview
- **Real-time Metrics**: Active users, total registered, scheduled deletions, latency, error rate
- **Performance Charts**: TPS, latency, DB connections, CPU/disk load, queries per request, memory, error trends
- **Endpoint Analysis**: Performance metrics for all API endpoints
- **Telemetry History**: Historical performance data with customizable time ranges

### User Management
- **User Search**: Search users by username
- **Tier Management**: View and modify user tiers (Free, Alpha, Beta, Premium, Staff)
- **Badge Management**: Add/remove user badges
- **User Actions**: Kill sessions, suspend users, force rename
- **Internal Notes**: Add private notes to user accounts
- **Admin User CRUD**: Create, edit, delete, enable/disable admin users with role assignment

### Security
- **Admin Account Management**: Change password, manage 2FA, regenerate backup codes
- **IP Access Control**: Block/unblock IP addresses
- **Username Blacklist**: Manage banned username patterns
- **API Access Tokens**: Create, manage, and revoke API tokens with IP scoping
- **Content Moderation**: Review image, message, and user reports
- **Hash Blocking**: Block specific image hashes
- **User Blocking**: Block users with duration settings

### Database Migrations
- **Migration Status**: View applied, pending, and failed migrations
- **Run Migrations**: Execute pending migrations with dry-run support
- **Irreversible Protection**: Safety checks for irreversible migrations
- **Emergency Override**: Generate emergency tokens for urgent migrations
- **Migration Details**: View detailed logs and execution information

### AutoMod
- **Configuration**: Configure OpenAI, Perspective, and custom AI providers
- **Rule Management**: Create, edit, and delete AutoMod rules
- **Rule Types**: Keyword, regex, message spam, mention spam, invite links, etc.
- **Actions**: Configure automated moderation actions

### System Logs
- **Log Viewing**: View system logs with filtering
- **Live Updates**: Optional live log streaming
- **Log Search**: Search logs with grep functionality

### Roles & Approvals
- **Role Management**: Create and manage admin roles with permissions
- **Approval System**: Manage approval requests for sensitive operations

## Usage

### Setup (in main.py)

```python
from src.core import admin
admin.setup(db, auth_module)
```

### Check Admin Status

```python
from src.core import admin

if admin.is_admin(user_id):
    # User has admin privileges
    pass
```

### Manage Tickets

```python
from src.core import admin

# Get all open tickets
tickets = admin.get_feedback_tickets(status_filter='open')

# Update ticket status
admin.update_ticket_status(ticket_id, 'resolved', admin_user_id)

# Add internal note
admin.add_internal_note(ticket_id, admin_user_id, "Contacted user via email")
```

### Host Restriction

```python
from src.core import admin

allowed_hosts = ['127.0.0.1', 'localhost']
if admin.check_host_restriction(client_ip, allowed_hosts):
    # Allow access
    pass
```

## Configuration

Add to `config.yaml`:

```yaml
admin_ui:
  enabled: true
  path: /admin
  host_restriction:
    enabled: true
    allowed_hosts:
      - 127.0.0.1
      - localhost
  require_admin_role: true
```

## Database Schema

Adds `is_admin` column to `auth_users` table and creates:

```sql
CREATE TABLE admin_notes (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
```

Also adds columns to `feedback` table:
- `status` (TEXT)
- `resolved_at` (INTEGER)
- `resolved_by` (INTEGER)
- `internal_notes` (TEXT)

## API Endpoints

### Admin User Management
- `GET /api/v1/admin/users` - List admin users
- `POST /api/v1/admin/users` - Create admin user
- `PUT /api/v1/admin/users/{id}` - Update admin user
- `DELETE /api/v1/admin/users/{id}` - Delete admin user
- `POST /api/v1/admin/users/{id}/toggle-status` - Toggle user active status

### Migrations
- `GET /api/v1/admin/migrations` - List migrations
- `GET /api/v1/admin/migrations/{version}` - Get migration details
- `POST /api/v1/admin/migrations/{version}/run` - Run migration
- `POST /api/v1/admin/migrations/emergency-override` - Generate emergency token

## Frontend Structure

The admin dashboard is split into:
- `dashboard.html` - Main HTML template with tabbed interface
- `dashboard.js` - All JavaScript functionality (extracted from inline scripts)
- `dashboard.css` - Styling

Tabs include:
- Metrics
- Tickets
- Users (with Admin User Management)
- Deletions
- Security
- AutoMod
- Logs
- Roles
- Approvals
- Migrations (integrated from separate page)
