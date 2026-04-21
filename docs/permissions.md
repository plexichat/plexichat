# Permissions

Plexichat permissions are string-based capabilities such as `messages.send` or `admin.system`.

## Runtime Summary

- Permission categories: `{{PERMISSION_CATEGORY_COUNT}}`
- Total permissions: `{{PERMISSION_TOTAL_COUNT}}`

## Categories

{{PERMISSION_CATEGORY_BULLETS}}

## Permission Catalog

{{PERMISSION_DETAIL_BULLETS}}

## Usage Notes

- user and bot tokens may have different effective permission grants
- bot-restricted permissions should not be granted to automated accounts
- wildcard grants such as `messages.*` and `*` can expand access significantly
- application- or server-specific role systems can layer additional policy on top of these base permissions

## Related Pages

- [Authentication](api/authentication.md)
- [Features](features.md)
- [Security](security.md)
