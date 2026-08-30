# Get started with Plexichat

This guide helps you create an account, set up your profile, and use core features.

## Create your account

1. Go to your Plexichat server URL. Ask your administrator for the URL if you do not have it.
2. Click **Register** or **Sign Up**.
3. Enter your username (3–32 characters, letters, numbers, and underscores), your email address, and a password that meets the server's password policy.
4. Complete email verification if the server requires it.

If registration is closed, you need an invite link from an administrator or an existing member.

## Log in

1. Enter your username and password on the login page.
2. If you use two-factor authentication (2FA), open your authenticator app and enter the 6-digit code when prompted.

If you are locked out after too many failed attempts, wait for the lockout period to expire before you try again.

## Set up your profile

After your first login, update your profile:

- **Display name** — How your name appears to others. This is separate from your username.
- **Avatar** — Upload an image up to 5 MB. Supported formats include JPEG, PNG, GIF, and WebP.

## Understand the interface

### Server list

The left sidebar shows servers you belong to and your direct messages. Click a server to open it. Click **Add Server** to create a community or join one with an invite.

### Channels

Each server organizes channels into categories:

- **Text channels** (marked with `#`) for typed conversations.
- **Voice channels** (marked with a speaker icon) for voice.

### Message area

- Type a message and press **Enter** to send it.
- Use the attachment button or drag and drop files.
- Hover over a message to reply, add a reaction, or manage the message if you have permission.

## Key actions

### Mentions

- `@username` — Mention a specific person. They receive a notification.
- `@here` — Mention everyone who is currently online in the channel.
- `@everyone` — Mention everyone in the server, if you have permission.
- `@role` — Mention everyone with a specific role.

### Format text

You can format messages with Markdown:

- `**bold**` for **bold**
- `*italic*` for *italic*
- `` `code` `` for `code`
- ` ```code block``` ` for a multi-line code block

### Attach files

Click the attachment button or drag and drop files into the message input. Limits depend on server configuration. Defaults are defined in `src/config_defaults.py:210`:

- Images — Up to 10 MB (JPEG, PNG, GIF, WebP)
- Video — Up to 100 MB (MP4, WebM, QuickTime)
- Audio — Up to 50 MB (MP3, OGG, WAV, WebM)
- Documents — Up to 25 MB (PDF, plain text, ZIP, Markdown, JSON)

### Add reactions

1. Hover over a message.
2. Click the emoji button.
3. Choose an emoji. To add your vote to an existing reaction, click the reaction.

### Threads

Use threads for focused discussions without cluttering the channel.

1. Hover over a message and click the thread icon.
2. Send your reply in the thread.

Channel moderators can archive threads to close the discussion.

### Pin messages

Users with the **Manage Messages** permission can pin important messages. Click the pin icon at the top of the channel to view pinned messages.

## Search

1. Click the search button in the channel header.
2. Enter your query and press **Enter**.

Filter examples:

- `from:username` — Messages from a specific user.
- `in:#channel` — Messages in a specific channel.
- `has:image` — Messages that contain images.
- `before:YYYY-MM-DD` and `after:YYYY-MM-DD` — Messages within a date range.

## Notifications

### Check notifications

Click the bell icon to view unread notifications. You receive a notification when someone mentions you.

### Change notification settings

1. Go to **User Settings** > **Notifications** to set global preferences such as desktop notifications and sound.
2. To change per-server settings, right-click the server icon and select **Notification Settings**.
3. To change per-channel settings, right-click the channel and select **Notification Settings**.

Options include:

- **All messages** — Notify for every message.
- **Only mentions** — Notify only when you are mentioned.
- **Nothing** — Turn off notifications for that server or channel.

To mute a channel or server, right-click it and select **Mute**, then choose a duration.

## Privacy and safety

### Block a user

1. Open the user's profile.
2. Click **Block**.

Blocked users cannot send you direct messages, and their messages are hidden from you.

### Report content

1. Right-click the message.
2. Click **Report**.
3. Select a reason.

Server moderators review the report.

### Turn on two-factor authentication

1. Go to **Settings** > **Security**.
2. Click **Enable Two-Factor Authentication**.
3. Scan the QR code with your authenticator app and enter the code.
4. Save your backup codes. Use them to recover your account if you lose access to your authenticator.

For more details, see [Two-factor authentication](./two-factor-authentication.md) and [Password guidance](./password-guidance.md).

## Manage your account

### Delete your account

1. Go to **Settings** > **Account**.
2. Click **Delete Account** and confirm.

Your account enters a grace period (typically 30 days). Log in during that period to cancel the deletion. After the grace period, the server permanently removes your account and removes you from all servers. Messages you sent may be anonymized to preserve conversation context. Files, direct messages, and audit log entries are handled according to server policy.

## Create and manage servers

### Create a server

1. Click **Add Server**.
2. Choose **Create My Own** or a template.
3. Enter a name and upload an icon (512×512 pixels recommended).
4. Click **Create**.

### Server settings

Right-click the server icon and select **Server Settings**. Key sections include:

- **Overview** — Name, icon, and description.
- **Roles** — Create and manage roles and permissions.
- **Channels** — Create and organize channels.
- **Moderation** — Configure moderation tools.
- **Members** — View and manage members.

### Manage channels

To create a channel, click the **+** next to a category, choose **Text Channel** or **Voice Channel**, set a name and permissions, and click **Create Channel**. You can organize channels into collapsible categories and sync permissions from another channel.

### Manage roles

Go to **Server Settings** > **Roles** and click **Create Role**. Set a name, color, and permissions, then save. Roles are ordered by hierarchy, and you can drag them to reorder. Members can have multiple roles.

Common permissions include **Administrator**, **Manage Server**, **Manage Roles**, **Manage Channels**, **Kick Members**, **Ban Members**, **Manage Messages**, **Send Messages**, **Connect**, and **Speak**. Use the **Administrator** permission with caution.

### Moderate members

Right-click a member in the member list to kick, ban, time out, mute, deafen, move, or assign a role.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Enter` | Send message if Enter-to-send is disabled |
| `Escape` | Cancel editing or close a dialog |
| `Up Arrow` | Edit your last message when the input is empty |
| `Ctrl+E` | Open the emoji picker |
| `Ctrl+Shift+M` | Toggle mute |
| `Ctrl+Shift+D` | Toggle deafen |

## Get help

Contact your server administrator or community moderators if you need help with your account. If you administer the server, see the deployment documentation.
