# Security logout

You have been signed out of one or more sessions. Plexichat revokes the session token and clears the browser cookie.

This page explains why it happened and what to do next. API details are in `src/api/routes/auth/sessions.py` and `src/core/auth/managers/session.py`.

## Why you were signed out

- **You signed out.** `POST /api/v1/auth/logout` revokes your current session (`revoked = 1`) and clears the `HttpOnly` session cookie.
- **You or an administrator revoked sessions.** `DELETE /api/v1/auth/sessions/{session_id}` revokes a single session. `POST /api/v1/auth/sessions/revoke-all` revokes all sessions or all except the current one (`{"except_current": true}`).
- **Your session expired.** Sessions expire after `authentication.sessions.expire_hours` (default 720 hours / 30 days). Expired tokens return `TokenExpiredError`.
- **Your session was rotated or invalidated.** `POST /api/v1/auth/refresh` rotates the token. The old token is invalid after rotation. Revoking a session invalidates cached verification (`token_verify:*`).
- **Administrator or security action.** Disabling TOTP 2FA revokes all other sessions. An administrator can also freeze an account scheduled for deletion.

## What to do next

1. Sign in again with your username and password. If you use 2FA or passkeys, complete that step.
2. Review active sessions: `GET /api/v1/auth/sessions` returns `id`, `ip_address`, `user_agent`, `created_at`, `expires_at`, `last_activity`, and `current`.
3. If you see a session you do not recognize, revoke it or use `POST /api/v1/auth/sessions/revoke-all`.

If you believe the sign-out was a mistake, contact your server administrator.

## Session behavior to know

- **Maximum sessions:** `authentication.sessions.max_per_user` (default 10). When you exceed the limit, the oldest sessions are revoked.
- **Activity extension:** If `authentication.sessions.extend_on_activity` is `true` (default), `last_activity` updates after 60 seconds of inactivity and `expires_at` extends when you are within `extend_threshold_hours` (default 24 hours) of expiry. Set `extend_threshold_hours` to `0` to extend on every activity.
- **Cookies and CSRF:** Browser sessions use an `HttpOnly` cookie. Refresh via cookie requires the `X-Plexichat-Request: 1` header to prevent CSRF.
- **Trusted devices (2FA skip).** A device fingerprint you explicitly marked trusted via `POST /api/v1/auth/devices/trust` can sign in without re-entering a TOTP code. Trust is stored server-side on the device row, never in any client-controlled value, and a missing or unknown fingerprint can never match. Revoke trust any time with `POST /api/v1/auth/devices/untrust`; revoking the device also removes its 2FA skip.

## For administrators

- To sign out a single user from all devices, call `logout_all(user_id)` or `POST /api/v1/auth/sessions/revoke-all` as that user.
- To sign out all users, the server can call `logout_all_users()`.

## Get help

- Try signing in again and check your password and 2FA code.
- Review your sessions and revoke unknown ones.
- Contact your server administrator if you cannot sign in or suspect unauthorized access.
