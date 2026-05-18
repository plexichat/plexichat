# Passkeys (WebAuthn/FIDO2)

Passkeys provide a secure, passwordless way to sign in to your Plexichat account using biometric authentication (fingerprint, face recognition) or a device PIN. Passkeys are built on the WebAuthn/FIDO2 standard and offer stronger security than traditional passwords.

## What are Passkeys?

Passkeys are a modern authentication method that replaces passwords with cryptographic credentials stored on your device. When you sign in with a passkey:

- Your device verifies your identity using biometrics (fingerprint, face) or a PIN
- Your device cryptographically signs a challenge from the server
- The server verifies the signature and authenticates you

**Key benefits:**
- **Phishing-resistant**: Passkeys only work on the legitimate website
- **No passwords to remember**: Your device handles authentication
- **Stronger security**: Cryptographic keys are much harder to steal than passwords
- **Convenient**: Use Face ID, Touch ID, or Windows Hello instead of typing passwords

## Setting Up Passkeys

### Requirements

To use passkeys, you need:

- A device that supports WebAuthn/FIDO2:
  - **macOS**: macOS 12 or later with Touch ID or a compatible security key
  - **Windows**: Windows 10/11 with Windows Hello or a compatible security key
  - **iOS/iPadOS**: iOS 16 or later with Face ID or Touch ID
  - **Android**: Android 9 or later with biometric authentication
  - **Linux**: A browser with WebAuthn support and a compatible security key
- A modern web browser with WebAuthn support:
  - Chrome 67+
  - Firefox 60+
  - Safari 13+
  - Edge 18+

### Registering Your First Passkey

1. **Sign in to your account** using your password or 2FA code
2. **Navigate to Settings** -> **Security** -> **Passkeys**
3. **Click "Add Passkey"**
4. **Enter a device name** (e.g., "iPhone 15 Pro", "Work Laptop")
5. **Follow your device's prompts** to create the passkey:
   - Use Touch ID, Face ID, or Windows Hello
   - Or insert and tap your security key
6. **Confirm the passkey** is registered

Your passkey is now ready to use for sign-in!

### Adding Multiple Passkeys

You can register multiple passkeys for different devices:

1. Go to **Settings** -> **Security** -> **Passkeys**
2. Click **"Add Passkey"**
3. Repeat the registration process on each device

**Recommended practice:** Register at least two passkeys on different devices to prevent lockout if one device is lost.

## Signing In with Passkeys

### Using a Passkey on the Same Device

1. Visit the Plexichat sign-in page
2. Enter your **username** (if prompted)
3. Click **"Sign in with Passkey"**
4. Your device will prompt for biometric authentication or PIN
5. Once verified, you'll be signed in automatically

### Using a Passkey on a Different Device (Cross-Device)

If you have a passkey on your phone but want to sign in on your computer:

1. Visit the Plexichat sign-in page on your computer
2. Enter your **username**
3. Click **"Sign in with Passkey"**
4. A QR code will appear
5. Scan the QR code with your phone's camera
6. Your phone will prompt for biometric authentication
7. Once verified, your computer will sign you in automatically

This works across platforms (e.g., use your iPhone to sign in on Windows).

## Managing Your Passkeys

### Viewing Your Passkeys

1. Go to **Settings** -> **Security** -> **Passkeys**
2. You'll see a list of all registered passkeys with:
   - Device name
   - Date added
   - Last used date
   - Backup status

### Renaming a Passkey

1. Go to **Settings** -> **Security** -> **Passkeys**
2. Find the passkey you want to rename
3. Click the **edit icon** or **"Rename"**
4. Enter the new device name
5. Click **"Save"**

### Revoking a Passkey

If you lose a device or want to remove a passkey:

1. Go to **Settings** -> **Security** -> **Passkeys**
2. Find the passkey you want to remove
3. Click the **delete icon** or **"Revoke"**
4. Confirm the removal

**Important:** After revoking a passkey, it cannot be used to sign in. Make sure you have at least one other passkey or remember your password before removing your last passkey.

## Passkey Backup and Sync

### Cloud-Synced Passkeys

Some platforms automatically sync passkeys across your devices:

- **Apple iCloud Keychain**: Syncs passkeys across iPhone, iPad, Mac, and Apple TV
- **Google Password Manager**: Syncs passkeys across Android devices and Chrome
- **1Password, Bitwarden, etc.:** Password managers that support passkeys

When a passkey is synced, it's marked as "backed up" in your passkey list. This means you can recover it if you lose your device.

### Single-Device Passkeys

Passkeys that are not synced only exist on that specific device. If you lose the device, you lose the passkey.

**Recommendation:** Always enable passkey sync if available, or register multiple passkeys on different devices.

## Security Best Practices

### Enable Multiple Passkeys

Register passkeys on at least two different devices to prevent lockout:
- Primary device (e.g., your main phone)
- Backup device (e.g., a tablet or secondary phone)

### Keep Your Device Secure

Since passkeys rely on device authentication:
- **Enable device lock**: Use a strong PIN, password, or biometric lock
- **Keep software updated**: Install security updates promptly
- **Use device encryption**: Ensure full disk encryption is enabled

### Revoke Lost Devices Immediately

If you lose a device with a passkey:
1. Sign in from another device
2. Revoke the lost passkey immediately
3. Register a new passkey on a replacement device

### Understand Platform Limitations

- **Platform-specific**: Passkeys created on Apple devices may not work on Android, and vice versa
- **Browser support**: Some older browsers don't support WebAuthn
- **Corporate devices**: Some organizations restrict passkey usage

## Troubleshooting

### "Passkey not supported" Error

**Causes:**
- Your browser doesn't support WebAuthn
- Your device doesn't have compatible hardware
- You're using an outdated operating system

**Solutions:**
- Update your browser to the latest version
- Update your operating system
- Try a different browser (Chrome, Firefox, Safari, Edge)
- Use a hardware security key as an alternative

### "Passkey registration failed" Error

**Causes:**
- The server's passkey configuration is incorrect
- Your device's secure element is unavailable
- Network connectivity issues

**Solutions:**
- Try again with a stable internet connection
- Restart your device
- Contact your server administrator if the issue persists

### "Challenge expired" Error

**Causes:**
- The registration/authentication process took too long
- Network delays

**Solutions:**
- Start the process again
- Ensure you have a stable internet connection
- Complete the biometric prompt promptly

### Can't Sign In After Losing All Passkeys

If you lose all devices with passkeys and don't remember your password:

1. Use the **password reset** feature if you have access to your email
2. Contact your server administrator for account recovery
3. If self-hosting, use the admin panel to reset your password

**Prevention:** Always keep at least one backup authentication method (password or another passkey).

## Passkeys vs. Passwords

| Feature | Passkeys | Passwords |
|---------|----------|-----------|
| **Phishing resistance** | [X] Excellent | [ ] Vulnerable |
| **Reusability risk** | [X] Unique per site | [ ] Often reused |
| **Ease of use** | [X] Biometric/PIN | [!] Must remember |
| **Device dependency** | [!] Requires device | [X] Works anywhere |
| **Recovery** | [!] Requires backup | [X] Can reset via email |
| **Security** | [X] Cryptographic | [!] Can be stolen |

## FAQ

**Q: Can I use both passkeys and passwords?**  
A: Yes! You can use passkeys for convenience and keep your password as a backup.

**Q: What happens if I lose my phone with my passkey?**  
A: Revoke the passkey from another device and register a new one. If you have no other devices, use password reset.

**Q: Are passkeys stored on the server?**  
A: No, only the public key is stored. The private key never leaves your device.

**Q: Can someone use my passkey if they steal my device?**  
A: Only if they can bypass your device's lock screen (PIN, biometrics). Always use strong device security.

**Q: Do passkeys work with 2FA?**  
A: Passkeys are considered a form of 2FA themselves. You don't need additional 2FA when using passkeys.

**Q: Can I use a hardware security key instead of biometrics?**  
A: Yes! Security keys like YubiKey are fully supported and offer excellent security.

**Q: Are passkeys accessible to users with disabilities?**  
A: Many platforms offer alternative authentication methods. Check your device's accessibility settings for options.

## Getting Help

If you encounter issues with passkeys:

1. Check the [troubleshooting section](#troubleshooting) above
2. Ensure your browser and OS are up to date
3. Try a different browser
4. Contact your server administrator
5. Check the [Plexichat documentation](https://docs.plexichat.com) for more information

## Related Documentation

- [Password Guidance](./password-guidance.md) - Creating strong passwords
- [Two-Factor Authentication](./two-factor-authentication.md) - Using TOTP as an alternative
- [Security Best Practices](../security.md) - General account security
- [Account Deletion](./access-blocked.md) - What happens when you delete your account
