# Admin Templates

HTML templates and associated assets for the admin dashboard UI. These are server-rendered pages served alongside the main API.

## Files

### `login.html`
Admin login page:
- Username/password form
- 2FA TOTP input
- Session timeout warnings
- IP restriction messages
- Error state handling

### `dashboard.html`
Admin dashboard main page:
- System statistics widgets
- Active user/message/storage charts
- Recent activity feed
- Quick action buttons
- Status indicators

### `dashboard.css`
Dashboard styling:
- Admin color scheme (distinct from main app)
- Responsive grid layout
- Data visualization styles
- Dark mode support

### `dashboard.js`
Dashboard interactivity:
- Real-time stat updates via WebSocket
- Chart rendering and data fetching
- Widget configuration and drag-drop
- Auto-refresh polling

### `migrations.html`
Migration management page:
- Migration status table (version, name, checksum, status, date)
- Run pending migrations button
- Rollback controls
- Integrity check results display
