# Password guidance

Your password is the first line of defense for your Plexichat account. This guide explains how Plexichat validates passwords and how to create a strong password.

## Why password security matters

Attackers use automated tools to try common passwords and leaked credentials.

Common methods:

- Brute force — trying many combinations.
- Dictionary attacks — trying common words and leaked passwords.
- Credential stuffing — reusing passwords leaked from other breaches.
- Rainbow tables — precomputed hash reversals (mitigated by Argon2id hashing).

Weak passwords use short length, common words (`password`, `123456`), predictable patterns (`Password123!`), or appear in breach databases.

## How Plexichat protects your password

- **Argon2id hashing.** Passwords are hashed with Argon2id. The database never stores plaintext. The server also uses a dummy hash to equalize timing when a username does not exist.
- **Rate limiting and lockout.** Failed logins are throttled per user and IP. Repeated failures from distinct IPs can lock the account for a configured duration.
- **Breach list check.** Passwords are checked against a bundled common-password list.

## Password requirements

Plexichat validates passwords in `src/core/auth/passwords.py` through `validate_password()` and `get_password_config()`. Requirements come from `authentication.password` in server config. Defaults are:

- Minimum length: 12 characters
- Maximum length: 128 characters
- Must contain at least one uppercase letter (`A-Z`)
- Must contain at least one lowercase letter (`a-z`)
- Must contain at least one digit (`0-9`)
- Must contain at least one special character (`!@#$%^&*(),.?":{}|<>_-+=[]\;'/`~` and similar)
- Must not be in the bundled common-password list (case-insensitive). The list is at `src/core/auth/data/common-passwords.txt` and contains 10,000 entries. The check uses `casefold()` matching and is enabled by default (`reject_common_passwords: true`).

The validator returns a `PasswordValidation` with `valid`, `score`, and `issues`. Score is 0 to 5:

- +1 for meeting minimum length
- +1 for each of uppercase, lowercase, digit, special
- +1 for length >= 16
- +1 for length >= 20
- Capped at 5

Server administrators can change any of these settings. If your server has different limits, the error messages from the validator are authoritative.

What Plexichat does not enforce:

- No check that the password contains your username or email.
- No check for simple variations of common passwords beyond the exact list match.

Example error messages:

- `Password must be at least 12 characters`
- `Password must be at most 128 characters`
- `Password must contain at least one uppercase letter`
- `Password is too common; choose a less predictable password`

## Create a strong password

### Use length

Length is the most important factor. A longer password or passphrase is harder to crack than a short complex one. Plexichat gives extra score points at 16 and 20 characters.

### Use a passphrase

A passphrase is a sequence of unrelated words you choose. It is easier to remember and more secure when long enough. Do not reuse published examples such as `correct-horse-battery-staple`, song lyrics, or quotes. Create your own random words.

### Avoid reuse and sharing

Use a unique password for Plexichat. Reusing a password means one breach compromises multiple accounts. Do not share passwords. Use your password manager's sharing feature if you must share access.

### Use a password manager

A password manager can generate random passwords, store them in an encrypted vault, and autofill them. Plexichat does not endorse a specific manager. Choose one that meets your security needs.

## Change your password

- `change_password(user_id, old_password, new_password)` requires your current password.
- `request_password_reset(email)` sends a reset email if email is configured.
- `reset_password(token, new_password)` completes the reset with the token from email.

All password changes go through `validate_password()`. A weak new password is rejected with `WeakPasswordError`.

## Two-factor authentication

Even with a strong password, enable two-factor authentication (2FA). See [Two-factor authentication](./two-factor-authentication.md).

Plexichat supports TOTP authenticator apps (6-digit codes, 30-second interval). You also receive backup codes in `XXXX-XXXX` format.

## Check for compromise

- Check if your email appears in known breaches at [Have I Been Pwned](https://haveibeenpwned.com/). Do not enter your Plexichat password on third-party sites.
- Many password managers alert you when a service you use is breached.

## If you suspect compromise

1. Change your Plexichat password immediately, and change it on any other service where you reused it.
2. Review active sessions at `GET /api/v1/auth/sessions` and revoke unknown sessions with `DELETE /api/v1/auth/sessions/{session_id}` or `POST /api/v1/auth/sessions/revoke-all`.
3. Enable 2FA if it is not already enabled.
4. Review recent messages, connections, and settings for unauthorized changes.
5. Report suspicious activity to your server administrator.

## Additional resources

- [NIST SP 800-63B: Digital Identity Guidelines](https://pages.nist.gov/800-63-3/sp800-63b.html)
- [CISA: Use Strong Passwords](https://www.cisa.gov/secure-our-world/use-strong-passwords)
- [Have I Been Pwned](https://haveibeenpwned.com/)

*Based on NIST SP 800-63B guidelines and Plexichat defaults as of 2026.*
