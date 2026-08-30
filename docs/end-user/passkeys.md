# Passkeys (WebAuthn/FIDO2)

Use passkeys to sign in to Plexichat without a password. A passkey is a cryptographic credential stored on your device and verified with biometrics, a device PIN, or a hardware security key.

Plexichat implements WebAuthn/FIDO2 through the `webauthn` Python library. If the library is not installed, passkey features are disabled and endpoints return `500`.

## How passkeys work

When you register a passkey, your device creates a public-private key pair. The private key never leaves your device. At sign-in, your device signs a server challenge and the server verifies the signature.

Benefits:

- Phishing-resistant. The credential is bound to the site's origin (`authentication.passkeys.origin` and `rp_id`).
- No password to remember for passkey sign-in.
- Stronger security than passwords alone.

A passkey does not replace your password unless you choose to use it exclusively. You can keep both.

## Requirements

- A device or browser that supports WebAuthn.
- A platform authenticator (Touch ID, Face ID, Windows Hello) or a roaming authenticator (FIDO2 security key).

Supported browsers include recent versions of Chrome, Firefox, Safari, and Edge. If your browser does not support WebAuthn, registration fails.

## Register a passkey

You must be signed in.

1. Go to **Settings > Security > Passkeys**.
2. Click **Add passkey**.
3. If prompted, enter a device name. If you do not provide one, the server stores `Unknown Device`.
4. Complete the browser's WebAuthn prompt (biometrics, PIN, or security key).

Behind the scenes:

- `POST /api/v1/auth/passkeys/options/register` creates a registration challenge. Rate limit: 5 requests per 60 seconds.
- `POST /api/v1/auth/passkeys/register` verifies the response. Rate limit: 3 requests per 60 seconds.
- The challenge expires after `challenge_ttl_seconds` (default 300 seconds). If it expires, start again.
- The server excludes credentials you already registered.

You can register multiple passkeys for different devices. To avoid lockout, register at least two.

## Sign in with a passkey

You do not need to be signed in.

1. On the sign-in page, click **Sign in with passkey**.
2. If you enter a username, the server returns `allowCredentials` for that user. If the username has no passkeys or does not exist, the server returns a dummy credential to avoid account enumeration (configurable with `authentication.passkeys.dummy_credential`). The endpoint is rate limited to 10 requests per 60 seconds.
3. Complete the browser prompt.

Behind the scenes:

- `POST /api/v1/auth/passkeys/options/authenticate` creates an authentication challenge.
- `POST /api/v1/auth/passkeys/authenticate` verifies the assertion. Rate limit: 5 requests per 60 seconds.

If you have TOTP two-factor authentication enabled, a successful passkey verification does not sign you in directly. The server returns `two_factor_required` with a `challenge_token` (valid for 300 seconds) and you must complete `POST /api/v1/auth/2fa` with a TOTP or backup code.

## Manage passkeys

### List passkeys

- `GET /api/v1/auth/passkeys` returns your active passkeys. Rate limit: 30 requests per 60 seconds.

Each entry includes:

- `id` — internal passkey ID
- `credential_id` — base64url-encoded credential ID
- `device_name` — name you provided or `Unknown Device`
- `device_type` — platform or cross-platform type, if reported
- `created_at` — creation time in milliseconds since epoch
- `last_used_at` — last authentication time, or `null`
- `backed_up` — whether the authenticator reported the credential as backed up
- `revoked` — always `false` for listed entries (revoked entries are filtered out)

In the UI, go to **Settings > Security > Passkeys** to view this list.

### Rename a passkey

- `PATCH /api/v1/auth/passkeys/{passkey_id}` with body `{"name": "New name"}`. Rate limit: 20 requests per 60 seconds.
- Returns `404` if the passkey does not exist or does not belong to you.

In the UI, find the passkey and click **Rename**.

### Revoke a passkey

- `DELETE /api/v1/auth/passkeys/{passkey_id}`. Rate limit: 10 requests per 60 seconds.
- Returns `404` if the passkey does not exist or is already revoked.
- Revoke is a soft delete (`revoked = 1`). The credential can no longer authenticate.
- If you revoke your last passkey, the server clears `passkey_enabled` for your account.

In the UI, find the passkey and click **Revoke** or **Delete**, then confirm.

If you lose a device, sign in from another device or with your password and revoke the lost device's passkey immediately.

## Challenge handling and security notes

- Challenges are single-use and expire after 300 seconds. Reusing a challenge returns `Invalid or expired challenge`.
- Verification requires the correct `rp_id` and `origin` (`https://{rp_id}` by default). When behind a TLS-terminating proxy, set `authentication.passkeys.rp_id` and `authentication.passkeys.origin` explicitly, for example `example.com` and `https://example.com`. Mismatched origins cause verification to fail.
- Authentication verifies the sign counter and updates `sign_count` and `last_used_at`.

## Backup and sync

Some authenticators sync passkeys through a cloud provider (iCloud Keychain, Google Password Manager, or a password manager). The `backed_up` flag reflects what the authenticator reported at registration. Plexichat does not change this value later.

A single-device passkey exists only on that device. If you lose the device and the passkey was not synced, you lose that passkey. Keep a password or a second passkey as a backup.

## Troubleshoot

### Passkey not supported

Your browser or device does not support WebAuthn. Update your browser and OS, try a different browser, or use a hardware security key.

### Registration fails

Check your network connection, retry the flow with a fresh challenge, and restart your device if the secure element is unavailable. If the problem persists, contact your server administrator.

### Challenge expired

You took too long to complete the prompt. Start the flow again and complete the biometric or PIN prompt promptly.

### Locked out

If you lose all passkeys and cannot remember your password, use password reset (if email is configured) or contact your server administrator.

## Related documentation

- [Password guidance](./password-guidance.md)
- [Two-factor authentication](./two-factor-authentication.md)
