# Two-Factor Authentication (2FA) Guide

Two-Factor Authentication (2FA) adds an extra layer of security to your Plexichat account. Even if someone steals your password, they cannot access your account without your 2FA code.

## What is 2FA?

2FA requires two different types of verification:

1. **Something you know** (your password)
2. **Something you have** (your phone or authenticator app)

## Setting Up 2FA

### Step 1: Open Security Settings

1. Click on your avatar in the top-right corner
2. Select **Settings** from the dropdown menu
3. In the left sidebar, click on **Security** under User Settings

### Step 2: Enable 2FA

1. In the Security section, find the **Two-Factor Authentication** card
2. Click the **Enable 2FA** button
3. A QR code will appear on your screen

### Step 3: Add to Your Authenticator App

You'll need an authenticator app that supports TOTP (Time-based One-Time Password). Popular options include:

- **[Google Authenticator](https://play.google.com/store/apps/details?id=com.google.android.apps.authenticator2)** (Android) / [iOS](https://apps.apple.com/app/google-authenticator/id388497605)
- **[Microsoft Authenticator](https://play.google.com/store/apps/details?id=com.azure.authenticator)** (Android) / [iOS](https://apps.apple.com/app/microsoft-authenticator/id983156458)
- **[Authy](https://authy.com)** (Android, iOS, Desktop)
- **[FreeOTP](https://freeotp.github.io)** (Android, iOS)
- **[AndOTP](https://github.com/andOTP/andOTP)** (Android)

**To add your account:**

1. Open your authenticator app
2. Tap the **+** or **Add Account** button
3. Select **Scan QR code** (or **Enter manually** if preferred)
4. Point your camera at the QR code on your screen
5. Your app will now show a 6-digit code that changes every 30 seconds

**Manual Entry Option:**

If you can't scan the QR code, you can enter the code manually:

1. In your authenticator app, choose **Enter manually** or **Enter key**
2. Copy the code shown under "Or enter this code manually" in Plexichat
3. Paste it into your authenticator app
4. The app will generate codes for your Plexichat account

### Step 4: Confirm Setup

1. Enter the 6-digit code from your authenticator app into the **Enter code from your app to confirm** field
2. Click **Confirm & Enable 2FA**
3. If the code is correct, 2FA will be enabled on your account

### Step 5: Save Your Backup Codes

After enabling 2FA, you'll see a list of **Backup Codes**:

1. Copy or write down each backup code
2. Store them in a safe place (password manager, safe, or secure note)
3. Each backup code can only be used once
4. Use these codes if you lose access to your authenticator app

**Important:** If you lose your authenticator app and don't have your backup codes, you may lose access to your account.

## Logging In with 2FA

Once 2FA is enabled, logging in requires an extra step:

1. Enter your username and password as usual
2. After entering your password, you'll be prompted for a 2FA code
3. Open your authenticator app and find the 6-digit code for Plexichat
4. Enter the code to complete login

**Note:** The code changes every 30 seconds. If it's about to change, wait for the new code before entering it.

## Disabling 2FA

If you need to disable 2FA:

1. Go to **Settings > Security**
2. In the Two-Factor Authentication section, click **Disable 2FA**
3. Enter your password
4. Enter a 2FA code from your authenticator app
5. Click **Disable 2FA**

**Warning:** Disabling 2FA reduces your account security. Only disable if absolutely necessary.

## Managing Active Sessions

You can view and manage all devices logged into your Plexichat account.

### View Active Sessions

1. Go to **Settings > Sessions**
2. Scroll to the **Active Sessions** section
3. You'll see a list of all devices currently logged in, including:
   - Device name (e.g., "Chrome on Windows", "Safari on iPhone")
   - Last active time
   - Location (if available)
   - Whether it's your current session

### Revoke a Single Session

To log out a specific device:

1. Find the session you want to revoke
2. Click the **Revoke** button next to that session
3. Confirm the action when prompted
4. That device will be immediately logged out

### Revoke All Other Sessions

To log out all devices except your current one:

1. Go to **Settings > Sessions**
2. Scroll to the **Active Sessions** section
3. Click the **Revoke All Other Sessions** button
4. Confirm the action when prompted
5. All other devices will be logged out immediately

**When to use this:**
- If you lost a device
- If you suspect unauthorized access
- After using a public computer
- If you want to secure your account

## Session Duration Settings

You can control how long you stay logged in:

1. Go to **Settings > Sessions**
2. Under **Session Settings**, select your preferred duration:
   - 24 hours
   - 3 days
   - 7 days (default)
   - 14 days
   - 30 days
3. Enable **Extend session on activity** to automatically extend your session when you're active

## Trusted Devices

You can skip 2FA on trusted devices for 30 days:

1. Go to **Settings > Sessions**
2. Under **Trusted Devices**, enable **Remember this device**
3. On your next login from this device, you won't need to enter a 2FA code

**Important:** Only enable this on personal devices you trust. Never enable on public or shared computers.

## Troubleshooting

### Lost Authenticator App

If you lose access to your authenticator app:

1. Use one of your backup codes to log in
2. Go to **Settings > Security**
3. Disable 2FA
4. Re-enable 2FA with a new authenticator app
5. Save your new backup codes

### Backup Codes Not Working

If your backup codes aren't working:

- Make sure you're entering the code exactly as shown (no spaces)
- Each backup code can only be used once
- If you've used all your backup codes, contact your server administrator

### 2FA Code Not Accepted

If your 2FA code is rejected:

- Make sure the code hasn't expired (codes change every 30 seconds)
- Check that your device's time is set correctly (automatic time sync is recommended)
- Try refreshing the code in your authenticator app
- If using manual entry, verify the secret key was entered correctly

### Can't Access Account Without 2FA

If you're locked out of your account and don't have backup codes:

- Contact your server administrator
- They may be able to help you recover your account through other verification methods
- This is why saving backup codes is critical

## Security Best Practices

### Protect Your Authenticator App

- Set a lock screen on your phone
- Enable biometric authentication (fingerprint, Face ID) on your authenticator app
- Don't share screenshots of your QR code or backup codes
- If your phone is lost or stolen, revoke your sessions immediately

### Backup Codes

- Store backup codes in a password manager
- Keep a physical copy in a safe location
- Never share backup codes with anyone
- Generate new backup codes if you suspect they've been compromised

### Trusted Devices

- Only enable trusted device on personal devices
- Don't enable on public computers or devices you don't control
- Revoke trusted devices if you sell or give away a device

## Additional Resources

### Password Security

For guidance on creating strong passwords, see the [Password Guidance](password-guidance.md) document.

### Passkeys (Passwordless Authentication)

For an even more secure and convenient authentication method, consider using [Passkeys](passkeys.md). Passkeys use biometric authentication or device PINs instead of passwords and provide phishing-resistant security.

### Account Security

- Use a unique password for Plexichat
- Enable 2FA on all your important accounts
- Review your active sessions regularly
- Be cautious of phishing attempts

### Support

If you need help with 2FA or account security:
- Contact your server administrator
- Check your server's support documentation

---

*Last updated: 2026*
