# Two-factor authentication (2FA)

Use two-factor authentication to add a second verification step to your Plexichat account. Even if someone learns your password, they cannot sign in without your authenticator code or backup code.

Plexichat supports Time-based One-Time Password (TOTP) 2FA compatible with Google Authenticator, Authy, and other TOTP apps. Implementation is in `src/core/auth/totp.py` and `src/core/auth/managers/twofactor.py`.

## How 2FA works

Plexichat uses TOTP with these defaults (from `authentication.totp` config):

- Digits: 6
- Interval: 30 seconds
- Window: 1 (accepts one interval of clock drift)
- Backup codes: 10 codes in `XXXX-XXXX` format (8 hex characters plus a dash). Codes are hashed with SHA-256 for storage.
- Replay protection: 90-second cache keyed by user, code, purpose, and actor.

## Set up 2FA

You must be signed in.

### 1. Start setup

Go to **Settings > Security** and click **Enable 2FA**. The server verifies your password without creating a new session and returns:

- `secret` — base32 TOTP secret (encrypted at rest)
- `qr_uri` — `otpauth://` URI for the QR code
- `backup_codes` — 10 one-time codes

Endpoint: `POST /api/v1/auth/2fa/enable` with `{"password": "..."}`. Returns `409` if 2FA is already enabled.

### 2. Add to your authenticator app

Scan the QR code or enter the secret manually:

1. Open your authenticator app.
2. Tap **Add account** or **+**.
3. Choose **Scan QR code** and point your camera at the code. If you cannot scan, choose **Enter key** and paste the secret.
4. The app shows a 6-digit code that changes every 30 seconds.

### 3. Confirm setup

Enter the current 6-digit code and submit.

- Endpoint: `POST /api/v1/auth/2fa/confirm` with `{"code": "123456"}`. Rate limit: 5 requests per 60 seconds.
- The server checks the code with replay protection (`setup:{user_id}`) and a per-user limit of 5 attempts per 300 seconds. Too many attempts returns `429`.
- On success, the server sets `totp_enabled = 1` and you can use 2FA at next sign-in.

### 4. Save your backup codes

Copy the 10 backup codes shown after setup. Store them in your password manager or a secure offline location. Each code can be used once. The server stores SHA-256 hashes, not plaintext.

If you lose your authenticator app and have no backup codes, contact your server administrator.

## Sign in with 2FA

1. Enter your username and password at `POST /api/v1/auth/login`.
2. If 2FA is enabled, the server returns `two_factor_required` with a `challenge_token` and `methods: ["totp", "backup_code"]`. The challenge expires in 300 seconds and is bound to the IP and user agent that created it.
3. Submit the code at `POST /api/v1/auth/2fa` with `{"challenge_token": "...", "code": "123456"}`. Rate limit: 5 requests per 60 seconds.

The server verifies:

- The challenge exists, is unused, and matches the stored hash.
- IP and user-agent blind indexes match (if present).
- The per-user limit (5 attempts per 300 seconds) and per-challenge limit (3 attempts) are not exceeded.
- The TOTP code (with replay cache) or a backup code. A valid backup code is consumed atomically; the remaining hashes are updated.

On success, the server creates a new session and returns a session token. The response also sets the `HttpOnly` session cookie.

Passkey sign-in also requires 2FA when `totp_enabled` is true. A successful WebAuthn verification returns `two_factor_required` and you complete the same `POST /api/v1/auth/2fa` step.

## Manage 2FA

### Check status

- `GET /api/v1/auth/2fa/status` returns `{"enabled": true|false, "backup_codes_remaining": number}`.

### Disable 2FA

1. Go to **Settings > Security**.
2. Click **Disable 2FA**.
3. Enter your password and a current 6-digit code.

- Endpoint: `POST /api/v1/auth/2fa/disable` with `{"password": "...", "code": "..."}`. Rate limit: 5 requests per 60 seconds.
- The server verifies the password and the TOTP code (separate replay key `disable:{user_id}`).
- On success, the server clears `totp_secret_encrypted` and `backup_codes_hash`, sets `totp_enabled = 0`, and revokes all other sessions for your account. Your current session stays active.
- Requires per-user rate limit of 5 attempts per 300 seconds.

### Regenerate backup codes

Use `regenerate_backup_codes(user_id, password)` (exposed to authenticated callers as needed). It verifies your password, generates 10 new codes, invalidates old codes, and returns the new ones. Store the new codes immediately.

## Manage active sessions

2FA and sessions are related but separate. Use sessions to review where you are signed in.

### View active sessions

- Go to **Settings > Sessions** or call `GET /api/v1/auth/sessions`.
- You see device info, IP address, user agent, creation time, expiry, last activity, and whether it is the current session.

### Revoke a single session

- In the UI, click **Revoke** next to the session.
- API: `DELETE /api/v1/auth/sessions/{session_id}`.

### Revoke all other sessions

- In the UI, click **Revoke all other sessions**.
- API: `POST /api/v1/auth/sessions/revoke-all` with `{"except_current": true}`. Use `false` to revoke the current session too; the server clears the cookie in that case.

## Session duration

Sessions are controlled by `authentication.sessions`:

- `expire_hours` — default 720 hours (30 days).
- `extend_on_activity` — default `true`.
- `extend_threshold_hours` — default 24 hours. The server extends `expires_at` when you are within this window of expiry, or on every activity if set to `0`.
- `max_per_user` — default 10 concurrent sessions.

## Trusted devices

You can mark a device as **trusted** so it skips the 2FA prompt at sign-in. Trust is stored server-side against the device fingerprint your client sends; a missing fingerprint is never trusted.

- List devices (trusted status included): `GET /api/v1/auth/devices`
- Trust a device: `POST /api/v1/auth/devices/trust` with `{"fingerprint": "..."}` — only devices you have signed in from can be trusted
- Remove trust: `POST /api/v1/auth/devices/untrust` with `{"fingerprint": "..."}`
- Forget a device entirely (revokes its sessions): `DELETE /api/v1/auth/devices/{device_id}`

If 2FA is disabled, all trusted flags stop mattering (the challenge is gone anyway). Revoking a device also revokes every session created from it. Only mark devices trusted on hardware you control.

## Troubleshoot

### Lost authenticator app

1. Sign in with a backup code at the 2FA prompt.
2. Go to **Settings > Security**, disable 2FA, and set it up again with a new app.
3. Save the new backup codes.

### Backup codes not working

- Enter the code exactly as shown, with or without the dash; verification is case-insensitive and ignores the dash.
- Each code can be used once.
- If you have used all codes, disable and re-enable 2FA to generate new ones, or contact your administrator.

### 2FA code not accepted

- Wait for the next 30-second interval and try the fresh code.
- Ensure your device clock is synchronized automatically.
- Check that you scanned the correct secret.

### Locked out

If you have no authenticator and no backup codes, contact your server administrator. They cannot recover your TOTP secret (it is encrypted) but can help with account recovery according to server policy.

## Security best practices

- Lock your phone and authenticator app with biometrics or a PIN.
- Do not share QR codes or backup codes.
- Store backup codes in a password manager and a secure offline copy. Never share them.
- If your device is lost or stolen, revoke its sessions immediately at **Settings > Sessions**.
- Do not enable long session durations on public or shared computers.

## Related documentation

- [Password guidance](./password-guidance.md)
- [Passkeys](./passkeys.md)
