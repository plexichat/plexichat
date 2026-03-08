# OAuth Scopes

OAuth scopes define which user data or application capabilities an external client is requesting during authorization.

## Runtime Summary

| Metric | Value |
|--------|-------|
| Total scopes | `{{OAUTH_SCOPE_COUNT}}` |
| Privileged scopes | `{{OAUTH_PRIVILEGED_SCOPE_COUNT}}` |
| Bot-required scopes | `{{OAUTH_BOT_SCOPE_COUNT}}` |

## Scope Catalog

| Scope | Privileged | Bot required | Description |
|-------|------------|--------------|-------------|
{{OAUTH_SCOPE_ROWS}}

## Usage Notes

- request the smallest scope set needed for the integration
- privileged scopes usually deserve extra review because they expose more sensitive data
- the `bot` scope is special because it changes the authorization flow toward bot installation behavior
- scope validation still happens server-side even if a client renders this list correctly

## Related Pages

- [Permissions](permissions.md)
- [Authentication](api/authentication.md)
- [Features](features.md)
