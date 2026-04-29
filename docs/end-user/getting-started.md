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

- `@username` - Mention a specific user (they'll get a notification)
- `@here` - Mention all online members in the current channel
- `@everyone` - Mention all members in the server (if you have permission)
- `@role` - Mention all members with a specific role

### Formatting

Plexichat supports basic text formatting in messages:

- `**bold**` -> **bold**
- `*italic*` -> *italic*
- `` `code` `` -> `code`
- ` ```code block``` ` -> multi-line code block

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
1. Go to Settings -> Security
2. Click **Enable Two-Factor Authentication**
3. Scan the QR code with your authenticator app
4. Enter the verification code to confirm
5. **Save your backup codes** - these are the only way to recover your account if you lose your authenticator

## Account Deletion

If you want to delete your account:
1. Go to Settings -> Account
2. Click **Delete Account**
3. Confirm the deletion

Your account enters a grace period (typically 30 days). During this time, you can cancel the deletion by logging back in. After the grace period, your account and data are permanently removed. Your messages may be anonymized (replaced with "[This message was sent by a deleted user]") to preserve conversation context.

**Data Retention:**
- Messages: May be anonymized but not deleted to preserve conversation context
- Servers: You are removed from all servers you were a member of
- Direct messages: Your DM conversations are removed
- Files: Your uploaded files may be deleted or retained based on server policy
- Audit logs: Your actions remain in server audit logs for accountability

**Cancellation:**
- Log in at any time during the grace period to cancel deletion
- Your account is restored with all data intact
- After the grace period expires, deletion cannot be reversed

## Creating and Managing Servers

### Creating a Server

1. Click the **Add Server** button (plus icon) in the server list
2. Choose **Create My Own** or start from a template
3. Enter a server name
4. Upload an icon (optional, 512x512px recommended)
5. Click **Create**

### Server Settings

Access server settings by right-clicking the server icon and selecting **Server Settings**:

- **Overview**: Server name, icon, and description
- **Roles**: Create and manage roles with permissions
- **Channels**: Create and organize text and voice channels
- **Moderation**: Configure moderation tools and auto-moderation
- **Members**: View and manage server members
- **Integrations**: Manage bots and webhooks
- **Audit Log**: View server moderation history

### Channel Management

**Creating Channels:**
1. Click the **+** next to a category or channel
2. Choose **Text Channel** or **Voice Channel**
3. Enter channel name
4. Set permissions (who can view, send messages, join voice)
5. Click **Create Channel**

**Channel Categories:**
- Organize channels into collapsible categories
- Set permissions at the category level
- Drag channels to reorder within categories

**Channel Permissions:**
- Override role permissions per channel
- Allow or deny specific actions (view, send, speak, connect)
- Use permission sync to copy permissions from another channel

### Role Management

**Creating Roles:**
1. Go to Server Settings -> Roles
2. Click **Create Role**
3. Enter role name
4. Choose role color
5. Set permissions using the permission tree
6. Click **Save Changes**

**Role Hierarchy:**
- Roles higher in the list have more permissions
- Drag roles to reorder hierarchy
- Members can have multiple roles
- Role colors are displayed in member lists

**Common Permissions:**
- **Administrator**: Full control of server (use with caution)
- **Manage Server**: Change server settings
- **Manage Roles**: Create and edit roles
- **Manage Channels**: Create and edit channels
- **Kick Members**: Remove members from server
- **Ban Members**: Permanently remove members
- **Mute Members**: Prevent members from speaking
- **Deafen Members**: Prevent members from hearing
- **Move Members**: Move members between voice channels
- **Manage Messages**: Delete any message
- **Send Messages**: Send text messages
- **Speak**: Use voice channels
- **Connect**: Join voice channels

### Member Management

**Moderating Members:**
1. Right-click a member in the member list
2. Select an action:
   - **Kick**: Remove from server (can rejoin with invite)
   - **Ban**: Permanently remove (cannot rejoin)
   - **Timeout**: Temporarily restrict actions
   - **Mute**: Prevent speaking in voice
   - **Deafen**: Prevent hearing in voice
   - **Move**: Move to different voice channel
   - **Assign Role**: Add or remove roles

**Viewing Member Info:**
- Click a member to view their profile
- See their roles, join date, and activity
- View mutual servers and friends

## Advanced Messaging Features

### Threads

Threads allow focused discussions within a channel without cluttering the main conversation.

**Creating a Thread:**
1. Hover over a message
2. Click the thread icon
3. Enter a thread name (optional)
4. Type your reply
5. Click **Send**

**Thread Features:**
- Threaded replies are grouped under the original message
- Thread count shows number of replies
- Click a thread to view and participate
- Threads can be archived to close discussion

### Message Pinning

Pin important messages for easy reference:

1. Hover over a message
2. Click the pin icon
3. Enter a pin reason (optional)
4. Click **Pin**

View pinned messages by clicking the pin icon at the top of the channel. Only users with the "Manage Messages" permission can pin messages.

### Bookmarks

Bookmark messages to find them later:

1. Hover over a message
2. Click the bookmark icon
3. View your bookmarks from your user menu

Bookmarks are personal and not visible to other users.

### Message Forwarding

Forward messages to other channels:

1. Hover over a message
2. Click the forward icon
3. Select destination channel
4. Add a comment (optional)
5. Click **Forward**

The forwarded message shows the original author and timestamp.

### Scheduled Messages

Schedule messages to be sent later:

1. Type your message in the input box
2. Click the schedule icon (clock)
3. Select a date and time
4. Click **Schedule**

Scheduled messages are sent automatically at the specified time. You can view and cancel scheduled messages from your user menu.

## Polls

Create polls to gather opinions from channel members.

**Creating a Poll:**
1. Click the poll icon in the message input
2. Enter your poll question
3. Add poll options (minimum 2, maximum 10)
4. Set poll duration (optional, defaults to 24 hours)
5. Click **Create Poll**

**Voting in Polls:**
- Click on an option to cast your vote
- You can change your vote until the poll closes
- Poll results are visible to all channel members

**Poll Results:**
- View current vote counts for each option
- Results update in real-time
- Poll creator can close the poll early
- Closed polls show final results

## Stickers

Use stickers to add personality to your messages.

**Using Stickers:**
1. Click the sticker icon in the message input
2. Browse available stickers
3. Click a sticker to send it

Stickers are larger than emoji and often animated. Server administrators can upload custom stickers for their community.

**Server Stickers:**
- Go to Server Settings -> Stickers
- Upload sticker images (PNG, GIF, WebP)
- Set sticker names for easy searching
- Manage sticker permissions

## Search

Search across messages, users, and servers.

**Message Search:**
1. Click the search icon in the channel header
2. Enter your search query
3. Use filters to narrow results:
   - `from:username` - Search messages from specific user
   - `in:#channel` - Search in specific channel
   - `has:image` - Search messages with images
   - `before:YYYY-MM-DD` - Search before date
   - `after:YYYY-MM-DD` - Search after date
4. Press Enter to search

**User Search:**
1. Click the search icon in the member list
2. Enter username or display name
3. Click on a user to view their profile

**Server Search:**
1. Click the search icon in the server list
2. Enter server name
3. Click on a server to view it (if you have an invite)

## Notification Management

Configure notifications to stay informed without being overwhelmed.

**Notification Settings:**
1. Go to User Settings -> Notifications
2. Configure global notification preferences:
   - Enable/disable desktop notifications
   - Set notification sound
   - Configure notification position
   - Set notification duration

**Per-Server Notifications:**
1. Right-click a server icon
2. Select **Notification Settings**
3. Choose notification level:
   - **All Messages**: Notify for all messages
   - **Only Mentions**: Notify only when mentioned
   - **Nothing**: Disable notifications for this server

**Per-Channel Notifications:**
1. Right-click a channel
2. Select **Notification Settings**
3. Choose notification level:
   - **All Messages**: Notify for all messages
   - **Only Mentions**: Notify only when mentioned
   - **Nothing**: Disable notifications for this channel

**Muting Channels:**
- Right-click a channel and select **Mute**
- Choose mute duration (15 min, 1 hour, 8 hours, 24 hours, Until I turn it back on)
- Muted channels don't show unread badges

**Muting Servers:**
- Right-click a server and select **Mute Server**
- Choose mute duration
- Muted servers don't show notification badges

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
