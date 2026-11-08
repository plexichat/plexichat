# OAuth scopes

OAuth scopes define which user data or application capabilities an external client requests during authorization. The server validates every requested scope, regardless of what the client displays.

Source of truth: `src/core/applications/oauth/scopes.py`.

## Runtime summary

*   Total scopes: `10`
*   Privileged scopes: `2`
*   Bot-required scopes: `1`

Privileged scopes expose sensitive data and require extra review. The `bot` scope changes the authorization flow to bot installation.

## Scope catalog

| Scope | Privileged | Bot required | Description |
|---|---|---|---|
| `applications.commands` | No | No | Create commands in servers you manage |
| `applications.commands.update` | No | No | Update application commands |
| `bot` | No | Yes | Add a bot to a server |
| `email` | No | No | Access your email address |
| `guilds` | No | No | Know what servers you are in |
| `guilds.join` | No | No | Join servers on your behalf |
| `guilds.members.read` | Yes | No | Read your member info in servers |
| `identify` | No | No | Access your username, avatar, and banner |
| `messages.read` | Yes | No | Read your messages |
| `webhook.incoming` | No | No | Create webhooks in channels you manage |

Definitions:

*   `VALID_SCOPES` — `src/core/applications/oauth/scopes.py`
*   `PRIVILEGED_SCOPES` — `src/core/applications/oauth/scopes.py` (`guilds.members.read`, `messages.read`)
*   `BOT_REQUIRED_SCOPES` — `src/core/applications/oauth/scopes.py` (`bot`)
*   `SCOPE_DESCRIPTIONS` — `src/core/applications/oauth/scopes.py`

## Validate scopes

Use `validate_scopes(scopes)` and `parse_scopes(scope_string)` from `src/core/applications/oauth/scopes.py`:

```python
from src.core.applications.oauth.scopes import validate_scopes, parse_scopes

scopes = parse_scopes("identify email guilds")
valid, issues = validate_scopes(scopes)
```

The server also enforces scopes during the OAuth flow. See `src/api/routes/auth/oauth.py` and `src/api/routes/auth/oauth_config.py`.

## Usage notes

*   Request the smallest scope set that your integration needs.
*   Privileged scopes require extra review because they expose sensitive data.
*   The `bot` scope is distinct because it triggers bot installation behavior. Include it only when you install a bot.
*   Scope validation happens server-side even if a client renders this list correctly.
*   OAuth clients must register allowed redirect URIs. The server rejects unlisted `redirect_uri` values. See `src/api/routes/auth/oauth.py`.

## Related pages

*   [Permissions](end-user/permissions.md)
*   [Authentication](api/authentication.md)
*   [Features](features.md)
*   [OAuth configuration](client-development/oauth-integration.md)
