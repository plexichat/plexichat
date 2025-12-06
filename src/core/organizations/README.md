# Organizations Module

Manages organization IDs, members, invites, and managed settings.

## Features

- **Organization Management**: Create and manage organizations
- **Member Management**: Add/remove members, assign roles
- **2-Step Invite Flow**: Secure invite process for existing users
- **Registration Codes**: Direct registration for new users
- **Managed Settings**: Org-locked user settings
- **Server Restrictions**: Allowlist/blocklist for servers
- **Root User Actions**: Password reset, account lock, force logout

## Usage

```python
from src.core import organizations

# Setup (done in main.py)
organizations.setup(db, auth)

# Create org (requires can_create_org feature)
org = organizations.create_org("acme", "Acme Corp", root_user_id)

# Invite existing user (2-step flow)
invite = organizations.create_invite(org.id, root_user_id, "existing", "username")
# User accepts
organizations.accept_invite(invite.id, user_id)
# Root approves
organizations.approve_invite(invite.id, root_user_id)

# Create registration code (for new users)
invite = organizations.create_invite(org.id, root_user_id, "registration")
# Code: ORG-acme-XXXX-XXXX-XXXX

# Root user actions
organizations.reset_user_password(root_user_id, target_user_id, "newpass")
organizations.lock_user(root_user_id, target_user_id)
organizations.force_logout(root_user_id, target_user_id)
organizations.disinherit_user(root_user_id, target_user_id)
```

## API Endpoints

See `src/api/routes/organizations.py` for full API documentation.
