# User Guide: Getting Started

Welcome to Plexichat! This guide covers everything you need to know to start using the platform as a regular user.

## Creating Your Account

1. Navigate to your Plexichat instance (ask your administrator for the URL)
2. Click **Register** or **Sign Up**
3. Enter your details:
   - **Username**: Your unique identifier (3-32 characters, letters, numbers, underscores)
   - **Email address**: For account verification and notifications
   - **Password**: Must meet your server's password policy (typically 12+ characters with uppercase, lowercase, digit, and special character)
4. Complete email verification if required by your server
5. Accept the terms of service if prompted

**Note**: Some servers have registration disabled (invite-only). If registration is closed, you'll need an invite link from an existing member or administrator.

## Logging In

1. Enter your username and password on the login page
2. If you have two-factor authentication (2FA) enabled:
   - Open your authenticator app (Google Authenticator, Authy, Microsoft Authenticator, etc.)
   - Enter the 6-digit code when prompted
3. If you're locked out after too many failed attempts, wait the lockout period (typically 15 minutes) and try again

## Setting Up Your Profile

After your first login, personalize your profile:

- **Display Name**: How your name appears to others (separate from your username)
- **Avatar**: Upload an image (up to 5MB, JPEG/PNG/GIF/WebP supported)
- **Bio/About Me**: A short description of yourself
- **Banner**: A header image for your profile page

## Understanding the Interface

### Server List (Left Sidebar)

- **Servers**: All communities you're a member of. Click to enter.
- **Direct Messages**: Private conversations with other users
- **Add Server**: Create a new community or join one with an invite

### Channel Area

When you select a server, you'll see its channels organized into categories:

- **Text channels** (marked with `#`): For typed conversations
- **Voice channels** (marked with a speaker icon): Join to talk with others via voice

### Message Area

- Type messages in the input box and press Enter to send
- Use the paperclip icon to attach files
- Use the emoji picker to add reactions to messages
- Hover over a message for options: reply, react, pin, edit (your own), delete (your own)

## Key Actions

### Mentions

- `@username` — Mention a specific user (they'll get a notification)
- `@here` — Mention all online members in the current channel
- `@everyone` — Mention all members in the server (if you have permission)
- `@role` — Mention all members with a specific role

### Formatting

Plexichat supports basic text formatting in messages:

- `**bold**` → **bold**
- `*italic*` → *italic*
- `` `code` `` → `code`
- ` ```code block``` ` → multi-line code block

### File Attachments

1. Click the paperclip icon or drag-and-drop files into the message input
2. Supported file types and size limits depend on server configuration:
   - Images: up to 10MB (JPEG, PNG, GIF, WebP)
   - Videos: up to 100MB (MP4, WebM, QuickTime)
   - Audio: up to 50MB (MP3, OGG, WAV, WebM)
   - Documents: up to 25MB (PDF, plain text, ZIP, Markdown, JSON)

### Reactions

1. Hover over any message
2. Click the emoji icon that appears
3. Choose an emoji from the picker
4. You can also click existing reactions on a message to add your own

### Pinned Messages

Channel moderators and administrators can pin important messages. View pinned messages by clicking the pin icon at the top of the channel.

## Notifications

- **Notification bell**: Click the bell icon in the top navigation to see your unread notifications
- **Desktop notifications**: Enable in your browser settings for real-time alerts
- **Mention notifications**: You'll be notified whenever someone mentions you with `@your-username`
- **Per-server settings**: Right-click a server icon to configure notification preferences per-server

## Privacy and Safety

### Blocking Users

If you encounter unwanted contact, you can block a user:
1. Open the user's profile
2. Click **Block**
3. Blocked users cannot send you direct messages, and their messages are hidden from you

### Reporting Content

If you see content that violates server rules:
1. Right-click the message
2. Select **Report**
3. Provide a reason for the report
4. Server moderators will review the report

### Two-Factor Authentication

Protect your account with 2FA:
1. Go to Settings → Security
2. Click **Enable Two-Factor Authentication**
3. Scan the QR code with your authenticator app
4. Enter the verification code to confirm
5. **Save your backup codes** — these are the only way to recover your account if you lose your authenticator

## Account Deletion

If you want to delete your account:
1. Go to Settings → Account
2. Click **Delete Account**
3. Confirm the deletion

Your account enters a grace period (typically 30 days). During this time, you can cancel the deletion by logging back in. After the grace period, your account and data are permanently removed. Your messages may be anonymized (replaced with "[This message was sent by a deleted user]") to preserve conversation context.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Enter` | Send message (if enter-to-send is disabled) |
| `Escape` | Cancel edit, close modal, deselect |
| `Up Arrow` | Edit your last message (when input is empty) |
| `Ctrl+E` | Open emoji picker |
| `Ctrl+Shift+M` | Toggle mute |
| `Ctrl+Shift+D` | Toggle deafen |

## Need Help?

- Check the [Deployment Guide](deployment.md) if you're a server administrator
- Check the [Getting Started Guide](getting-started.md) if you're a developer building a client or bot
- Contact your server administrator or community moderators for account issues
