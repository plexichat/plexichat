# Admin Module

Administrative functions for Plexichat server management.

## Overview

This module provides administrative capabilities including:
- Admin user management with `is_admin` flag
- Feedback/ticket viewing and management
- Internal notes on tickets
- Host restriction for admin access

## Features

- **Admin Flag**: Users can be marked as admins via `is_admin` column
- **Ticket Management**: View, filter, and update feedback tickets
- **Internal Notes**: Admins can add private notes to tickets
- **Host Restriction**: Admin UI can be restricted to localhost only
- **Status Tracking**: Tickets have status (open, in_progress, resolved, closed)

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
