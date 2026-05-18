# Creating Strong, Secure Passwords

Your password is the first line of defense for your Plexichat account. This guide explains how to create strong passwords that protect your personal information and communications, based on the recommendations from the National Institute of Standards and Technology (NIST).

**Important Note:** While Plexichat implements strict security measures-including Argon2id password hashing, rate limiting, and protection against common attacks-security ultimately rests in part with you. No system can provide complete protection if passwords are weak or reused across services.

## Why Password Security Matters

### The Reality of Password Attacks

Cybercriminals use automated tools that can attempt billions of password combinations per second. Weak passwords can be cracked in seconds or minutes, putting your account-and potentially your private messages, personal data, and connections-at risk.

**Common attack methods:**

- **Brute Force**: Trying every possible combination of characters
- **Dictionary Attacks**: Using lists of common words, phrases, and leaked passwords
- **Credential Stuffing**: Using passwords leaked from other breaches to access your accounts
- **Rainbow Tables**: Pre-computed tables to reverse hash functions

### What Makes a Password "Weak"

Passwords are considered weak when they:

- Use common words or phrases (password, 123456, qwerty)
- Include personal information (birthdays, pet names, addresses)
- Follow predictable patterns (Password123!, Summer2024)
- Have been exposed in previous data breaches
- Are short (less than 8 characters)
- Are reused across multiple services

## NIST Guidelines for Password Creation

The [National Institute of Standards and Technology (NIST) Digital Identity Guidelines](https://pages.nist.gov/800-63-3/sp800-63b.html) (NIST SP 800-63B) provides the authoritative framework for password security. Here's what NIST recommends:

### Length Over Complexity

**The most important factor is password length.** NIST research shows that length is more important than character complexity. Each additional character exponentially increases the time needed to crack a password.

**NIST Minimum:** 8 characters for user-chosen passwords
**Plexichat Requirement:** 12 characters minimum for enhanced security

NIST encourages users to make passwords as lengthy as they want, within reason. Longer passwords (or passphrases) are significantly more secure than shorter, complex ones.


### Blacklisting Common Passwords

NIST recommends comparing user-chosen passwords against a "black list" of unacceptable passwords, including:

- Passwords from previous breach databases
- Dictionary words
- Specific words users are likely to choose (like the service name)

Plexichat implements this by rejecting passwords in the top 10,000 most common passwords.

## How Plexichat Protects Your Password

Plexichat uses several security measures to protect your password:

- **Secure Hashing**: Your password is hashed using Argon2id, a modern algorithm designed to be resistant to brute-force attacks. Even if someone accesses the database, they cannot recover your password.
- **Rate Limiting**: Login attempts are limited to prevent automated guessing attacks.
- **Breach Protection**: Passwords are checked against lists of commonly used and compromised passwords.

## Password Requirements on Plexichat

When creating or changing your password on Plexichat, your password must:

- **Be at least 12 characters long** (exceeds NIST minimum of 8)
- **Not be in the top 10,000 most common passwords** (protected against dictionary attacks)
- **Not contain your username or email address**
- **Not be a simple variation of a common password** (e.g., Password123 -> Password1234)

**Note:** Server administrators can configure these requirements. The default rules require a minimum of 12 characters and include uppercase, lowercase, digit, and special character requirements.

## Creating Memorable Yet Secure Passwords

### Passphrases

A **passphrase**-a sequence of random words or a memorable sentence-is more secure and easier to remember than a complex password.

**Why passphrases work better:**

- Longer = exponentially harder to crack
- Easier to remember than random characters
- Natural language structure is still secure when long enough
- Typing is faster and less error-prone

**DO NOT use these example passphrases:**
- `correct-horse-battery-staple` (widely known from xkcd)
- Any passphrase from online examples or memes
- Famous quotes or song lyrics

Instead, create your own using:
- Random words you select yourself
- A memorable personal sentence with added numbers
- Unrelated words that have meaning only to you

### The Sentence Method

1. Think of a memorable sentence (15+ words)
2. Take the first letter of each word
3. Add numbers and symbols naturally

**Example (do not use this):**
- Sentence: "I moved to Seattle in March 2019 and started using Plexichat for secure communication!"
- Password: `ImtSiM2019asuPfsC!`

### The Personal Experience Method

Use details from your life that aren't publicly available:

- Childhood street address + year you moved
- First concert + your age at the time
- Pet's name + adoption date (if not shared on social media)

**Warning**: Only use this method if the information isn't on social media or public records.

## Password Managers

Password managers are tools that help you generate and store unique, complex passwords for each service you use. They are available as:

- Standalone applications (Bitwarden, 1Password, KeePassXC)
- Browser built-ins (Chrome Password Manager, Safari Keychain)
- Mobile device features (iCloud Keychain, Google Password Manager)

**What password managers do:**
- Generate cryptographically random passwords
- Securely store passwords in an encrypted vault
- Auto-fill passwords on websites and apps
- Sync across devices
- Alert you when passwords appear in breaches

**Important:** Plexichat does not endorse or recommend specific password managers. Choose one that fits your needs and security preferences.

## Two-Factor Authentication (2FA)

Even with a strong password, adding **Two-Factor Authentication (2FA)** provides an essential extra layer of security.

### What is 2FA?

2FA requires two different types of verification:

1. **Something you know** (your password)
2. **Something you have** (your phone, security key)

This means even if someone steals your password, they can't access your account without your physical device.

### TOTP Authenticator Apps (Supported by Plexichat)

Plexichat supports Time-based One-Time Password (TOTP) authenticator apps using the PyOTP library. These apps generate a 6-digit code that changes every 30 seconds.

**How it works:**
- Server and app share a secret key during setup
- Both calculate the same code using current time + secret
- Codes are synchronized and change simultaneously
- No internet connection needed after initial setup

**Common TOTP authenticator apps:**
- Google Authenticator
- Microsoft Authenticator
- Authy
- FreeOTP
- AndOTP

**Note:** Plexichat supports any TOTP-compliant authenticator app. Choose one that works for your device and preferences.

### Setting Up 2FA on Plexichat

1. Go to **Settings > Security > Two-Factor Authentication**
2. Click **Enable 2FA**
3. Scan the QR code with your authenticator app
4. Enter the 6-digit code to verify
5. **Save your backup codes** in a secure location (these restore access if you lose your device)

## Common Password Mistakes to Avoid

### Don't Reuse Passwords

**The Risk**: If one service is breached, all your accounts with that password are compromised.

**Solution:** Use unique passwords for each service. Password managers can help manage this.

### Don't Share Passwords

**The Risk:** Even with trusted people, shared passwords create accountability issues and increase exposure.

**Solution:** Use password manager sharing features for legitimate shared accounts.

### Don't Write Passwords Down

**The Risk:** Physical notes can be lost, stolen, or photographed.

**Solution:** Use a password manager. If you must write something down, only write the master password and store it in a secure location.

### Don't Ignore Breach Notifications

**The Risk:** Compromised passwords remain dangerous until changed.

**Solution:** Change passwords immediately when notified of a breach. Use [Have I Been Pwned](https://haveibeenpwned.com/) to check if your email appears in breaches.

## Checking if Your Password Has Been Compromised

### Have I Been Pwned

[haveibeenpwned.com](https://haveibeenpwned.com/) checks if your email or password appears in known data breaches.

- Enter your email to see which services were breached
- Use the password check to see if a specific password is compromised
- **Never enter your actual Plexichat password** into third-party sites

### Password Manager Breach Monitoring

Many password managers monitor for breaches and alert you when a service you use has been compromised.

## What to Do If You Suspect Your Password Is Compromised

### Immediate Steps

1. **Change your password immediately** on Plexichat and any other services using that password
2. **Review recent login activity** in your account settings
3. **Enable 2FA** if not already active
4. **Check for unauthorized sessions** and revoke any you don't recognize
5. **Check your email** for any notifications of account changes

### Check for Unauthorized Activity

On Plexichat, review:
- Recent messages sent (that you didn't send)
- Friends/connections added
- Server memberships
- Settings changes
- Login history

### Report Suspicious Activity

If you notice unauthorized activity:
- Report to your Plexichat server administrator
- Document what you observed
- Check linked email accounts for compromise

## Additional Resources

### Official NIST Guidelines

- **[NIST SP 800-63B: Digital Identity Guidelines - Authentication and Lifecycle Management](https://pages.nist.gov/800-63-3/sp800-63b.html)**
  - Appendix A: Strength of Memorized Secrets
  - The authoritative source for password requirements

### Educational Resources

- **[How Passwords Work - Cloudflare Learning](https://www.cloudflare.com/learning/privacy/what-is-password/)**
- **[Password Security Best Practices - CISA](https://www.cisa.gov/secure-our-world/use-strong-passwords)**
- **[Passwords - NCSC (UK)](https://www.ncsc.gov.uk/collection/top-tips-for-staying-secure-online/use-a-strong-and-separate-password-for-your-email)**

### Tools

- **[Have I Been Pwned](https://haveibeenpwned.com/)** - Check breach exposure

### Support

If you need help with your Plexichat account security:
- Contact your server administrator
- Check your server's support documentation

---

*Last updated: 2026 | Based on NIST SP 800-63B guidelines*
