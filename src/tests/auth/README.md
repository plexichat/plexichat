# Auth Tests

These tests verify the authentication and authorization module, including
login, registration, two-factor authentication, bot management, device
tracking, sessions, permissions, and audit logging.

## Test files

- `test_2fa.py` -- Set up, confirm, complete, and disable two-factor
  authentication using TOTP and backup codes.
- `test_audit.py` -- Record audit entries for login, logout, password changes,
  2FA events, and session revocation.
- `test_bots.py` -- Create, verify, regenerate tokens for, disable, enable,
  and delete bot accounts, and enforce bot limits and permission escalation
  rules.
- `test_devices.py` -- Track login devices, rename and revoke devices, trust
  devices for 2FA skip, and manage per-device session durations.
- `test_login.py` -- Authenticate users, issue tokens, handle invalid
  credentials, and manage session lifecycle.
- `test_passwords.py` -- Hash, verify, and validate password strength.
- `test_permissions.py` -- Check user and bot permissions against role-based
  access-control rules.
- `test_registration.py` -- Register new users, enforce username and email
  uniqueness, and validate input constraints.
- `test_sessions.py` -- Create, verify, revoke, and expire sessions, and
  handle session metadata.
