# PlexiChat Organization & Features Implementation - Continuation Guide

## CRITICAL: Context Gathering Required

**BEFORE DOING ANYTHING, YOU MUST READ THESE FILES:**

```
# Core READMEs
README.md
plexichat/README.md (if in workspace root) OR just README.md

# Foundation already implemented (READ THESE FIRST)
src/core/features/__init__.py
src/core/features/models.py
src/core/features/schema.py
src/core/features/exceptions.py
src/core/features/README.md

# Configuration (user_features and organizations config added)
main.py

# Existing auth system (you'll extend this)
src/core/auth/__init__.py
src/core/auth/manager.py
src/core/auth/models.py
src/core/auth/schema.py

# Existing API patterns
src/api/routes/auth.py
src/api/routes/users.py
src/api/routes/settings.py
src/api/routes/admin.py

# Rate limiting (integrate with features)
src/core/ratelimit/config.py
src/core/ratelimit/models.py

# Servers (for org server restrictions)
src/core/servers/schema.py
src/core/servers/models.py
```

For the client (in plexichat-client folder):
```
app.py
templates/login.html
templates/settings.html
```

---

## What Has Been Implemented (Foundation)

### 1. User Features Module (`src/core/features/`)

A complete module for managing:
- Feature flags (can_create_org, etc.)
- Profile badges (alpha_tester, staff, etc.)
- Rate limit tiers (standard, alpha, premium, staff)

**Key functions:**
- `features.get_user_tier(user_id)` - Get user's rate limit tier
- `features.get_tier_limits(tier)` - Get limits for a tier
- `features.get_rate_limit_multiplier(user_id)` - For rate limiting
- `features.has_feature(user_id, "can_create_org")` - Check feature flag
- `features.get_user_badges(user_id)` - Get profile badges
- `features.set_user_tier(user_id, admin_id, tier)` - Admin: set tier
- `features.add_badge(user_id, admin_id, badge)` - Admin: add badge

### 2. Configuration in main.py

Added to `get_default_config()`:
- `user_features` section with tier definitions (standard, alpha, premium, staff)
- `organizations` section with all org settings

---

## What You Need to Implement

### Phase 1: API Routes for Features (Admin)

Create `src/api/routes/features.py`:

```python
# Admin-only endpoints for managing user features
GET  /api/v1/admin/users/{user_id}/features     # Get user features
PUT  /api/v1/admin/users/{user_id}/features     # Update features
POST /api/v1/admin/users/{user_id}/badges/{badge}  # Add badge
DELETE /api/v1/admin/users/{user_id}/badges/{badge}  # Remove badge
PUT  /api/v1/admin/users/{user_id}/tier         # Set tier

# Public endpoint for viewing own features
GET  /api/v1/users/@me/features                 # Get own features/badges
```

**Rate limit these endpoints using the config keys:**
- `user_features.admin_rate_limit.max_per_minute`
- `user_features.admin_rate_limit.max_per_hour`

### Phase 2: Rate Limiting Integration

Modify `src/core/ratelimit/config.py`:

```python
# Add function to get user's rate limit multiplier
def get_user_multiplier(user_id: int) -> float:
    try:
        from src.core import features
        if features.is_setup():
            return features.get_rate_limit_multiplier(user_id)
    except:
        pass
    return 1.0
```

Modify rate limit middleware to use this multiplier.

### Phase 3: Organizations Module

Create `src/core/organizations/`:

```
organizations/
├── __init__.py
├── models.py
├── schema.py
├── manager.py
├── exceptions.py
└── README.md
```

**Database Schema (schema.py):**

```sql
-- Organizations table
CREATE TABLE organizations (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    root_user_id INTEGER NOT NULL,
    is_default INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    settings TEXT,
    default_servers TEXT,
    allowed_servers TEXT,
    blocked_servers TEXT,
    allow_invites INTEGER DEFAULT 1,
    invite_requires_approval INTEGER DEFAULT 1,
    FOREIGN KEY (root_user_id) REFERENCES auth_users(id)
);

-- Org members
CREATE TABLE org_members (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'member',
    joined_at INTEGER NOT NULL,
    invited_by INTEGER,
    FOREIGN KEY (org_id) REFERENCES organizations(id),
    FOREIGN KEY (user_id) REFERENCES auth_users(id)
);

-- Org managed settings
CREATE TABLE org_managed_settings (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL,
    setting_key TEXT NOT NULL,
    setting_value TEXT,
    locked INTEGER DEFAULT 1,
    FOREIGN KEY (org_id) REFERENCES organizations(id),
    UNIQUE(org_id, setting_key)
);

-- Org invites
CREATE TABLE org_invites (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL,
    code TEXT UNIQUE NOT NULL,
    invite_type TEXT NOT NULL,
    target_username TEXT,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER,
    max_uses INTEGER DEFAULT 1,
    uses INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    user_accepted INTEGER DEFAULT 0,
    user_accepted_at INTEGER,
    root_approved INTEGER DEFAULT 0,
    root_approved_at INTEGER,
    FOREIGN KEY (org_id) REFERENCES organizations(id)
);
```

**Modify auth_users table (in src/core/auth/schema.py):**
```sql
ALTER TABLE auth_users ADD COLUMN org_id INTEGER;
ALTER TABLE auth_users ADD COLUMN managed_by_org INTEGER DEFAULT 0;
```

**Key Manager Functions:**
- `create_org(name, display_name, root_user_id)` - Requires `can_create_org` feature
- `get_org(org_id)` / `get_org_by_name(name)`
- `add_member(org_id, user_id, role, invited_by)`
- `remove_member(org_id, user_id)` - "Disinherit" to default org
- `create_invite(org_id, root_user_id, invite_type, target_username=None)`
- `accept_invite(invite_id, user_id)` - Step 1 of 2-step flow
- `approve_invite(invite_id, root_user_id)` - Step 2
- `reset_user_password(root_user_id, target_user_id, new_password)`
- `lock_user(root_user_id, target_user_id)`
- `force_logout(root_user_id, target_user_id)`

### Phase 4: Auth Integration

Modify `src/core/auth/manager.py`:

1. Add `org_id` to User model in `src/core/auth/models.py`
2. Add `login_with_org(org_name, username, password)` method
3. Add `register_with_org_code(code, username, email, password)` method
4. Modify `register()` to assign to default org
5. Add org info to TokenInfo

### Phase 5: Organization API Routes

Create `src/api/routes/organizations.py`:

```python
# Auth routes (add to existing auth.py or new file)
POST /api/v1/auth/login/org              # Login with org
POST /api/v1/auth/register/org           # Register with invite code
GET  /api/v1/auth/org-invite/{code}      # Get invite info

# Org info
GET  /api/v1/orgs/@me                    # Get my org
GET  /api/v1/orgs/{org_id}               # Get org (if member)

# Root user management
GET  /api/v1/orgs/{org_id}/members
POST /api/v1/orgs/{org_id}/invites
GET  /api/v1/orgs/{org_id}/invites
DELETE /api/v1/orgs/{org_id}/invites/{id}
POST /api/v1/orgs/{org_id}/invites/{id}/approve

# User management (root only)
POST /api/v1/orgs/{org_id}/members/{user_id}/reset-password
POST /api/v1/orgs/{org_id}/members/{user_id}/lock
POST /api/v1/orgs/{org_id}/members/{user_id}/unlock
POST /api/v1/orgs/{org_id}/members/{user_id}/force-logout
POST /api/v1/orgs/{org_id}/members/{user_id}/disinherit

# Managed settings
GET  /api/v1/orgs/{org_id}/settings
PUT  /api/v1/orgs/{org_id}/settings/{key}

# Server restrictions
GET  /api/v1/orgs/{org_id}/servers
PUT  /api/v1/orgs/{org_id}/servers

# User invite flow
GET  /api/v1/users/@me/org-invites
POST /api/v1/users/@me/org-invites/{id}/accept
POST /api/v1/users/@me/org-invites/{id}/reject
```

**Rate limit using config keys:**
- `organizations.rate_limit.max_invites_per_hour`
- `organizations.rate_limit.max_member_actions_per_minute`

### Phase 6: Settings Integration

Modify `src/api/routes/settings.py`:

- Check if setting is org-locked before allowing changes
- Return `locked: true` in response for locked settings

### Phase 7: Server Restrictions

Modify `src/core/servers/schema.py`:
```sql
ALTER TABLE srv_servers ADD COLUMN org_id INTEGER;
ALTER TABLE srv_servers ADD COLUMN org_only INTEGER DEFAULT 0;
```

Modify server join logic to check org restrictions.

### Phase 8: Client Changes (in plexichat-client folder)

**New Flask routes in `app.py`:**
```python
@app.route("/login/org")
def login_org_page():
    code = request.args.get("code", "")
    return render_template("login_org.html", invite_code=code, default_server=config.DEFAULT_SERVER_ADDRESS)

@app.route("/register/org")
def register_org_page():
    code = request.args.get("code", "")
    return render_template("register_org.html", invite_code=code, default_server=config.DEFAULT_SERVER_ADDRESS)
```

**New templates:**
- `templates/login_org.html` - Org login page with org ID field
- `templates/register_org.html` - Org registration page with invite code field

**Modify existing:**
- `templates/login.html` - Add "Login with Organization" link at bottom
- `templates/settings.html` - Add org management section for root users, show lock icons on locked settings

---

## Important Implementation Notes

### 1. Default Org Creation

On server startup in `main.py` or organizations module setup, create default org if not exists:
```python
def _ensure_default_org():
    # Check if default org exists
    # If not, create it with admin as root
    # Migrate all existing users to default org
```

### 2. 2-Step Invite Flow

For existing users:
1. Root creates invite targeting username
2. User sees invite in their settings
3. User clicks "Accept" → `user_accepted = 1`
4. Root sees pending approval
5. Root clicks "Approve" → User moves to org

For new users (registration code):
1. Root creates registration invite (no target)
2. Gets long code like `ORG-acme-XXXX-XXXX-XXXX`
3. New user goes to `/register/org?code=ORG-acme-XXXX...`
4. User created directly in org (no approval needed)

### 3. Root User 2FA Requirement

Check `organizations.root_user.require_otp` config:
- On org creation, if true, root must have 2FA enabled
- If root doesn't have 2FA, return error prompting setup

### 4. Disinherit Flow

When root "disinherits" a user:
1. Remove from current org
2. Add to default org
3. Check `organizations.default_org.allow_users_to_leave`
4. If false, user is stuck in default org forever

### 5. Server Restrictions

Org can have:
- `default_servers` - Auto-join on org membership
- `allowed_servers` - Allowlist (NULL = all allowed)
- `blocked_servers` - Blocklist

Check on server join:
```python
def can_join_server(user_id, server_id):
    org = get_user_org(user_id)
    if server.org_only and server.org_id != org.id:
        return False
    if org.blocked_servers and server_id in org.blocked_servers:
        return False
    if org.allowed_servers and server_id not in org.allowed_servers:
        return False
    return True
```

---

## Testing

After implementation, test:

1. **Features API:**
   - Admin can set user tier
   - Admin can add/remove badges
   - User can view own features
   - Rate limits apply based on tier multiplier

2. **Organizations:**
   - Create org (requires can_create_org feature)
   - Invite existing user (2-step flow)
   - Create registration code
   - Register with code at /register/org
   - Root can reset password, lock, force logout
   - Disinherit moves to default org

3. **Settings:**
   - Org-locked settings show as locked
   - User cannot change locked settings

4. **Servers:**
   - Org-only servers restrict access
   - Allowlist/blocklist work correctly

---

## Git Commit & Push

After completing each phase, commit with descriptive message:

```bash
git add .
git commit -m "feat(features): Add user features API routes with rate limiting"
git push origin master
```

Final commit:
```bash
git add .
git commit -m "feat(organizations): Complete org ID system with invites, server restrictions, and client UI"
git push origin master
```

---

## Questions?

If anything is unclear, refer to:
- Existing module patterns in `src/core/`
- API route patterns in `src/api/routes/`
- The foundation code in `src/core/features/`
- Configuration in `main.py` under `user_features` and `organizations`
