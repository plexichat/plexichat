# Admin hardening notes

Admin routes use opaque sessions and permission scopes; they are not JWT endpoints.
State-changing and sensitive read operations require the corresponding
`require_admin_permission` scope. Destructive migration and bulk-reindex actions
also use the approval workflow where configured.

Search queries treat `%`, `_`, and backslashes literally. Scheduled-deletion
timestamps are represented in milliseconds, and admin database adapters may
return either mapping or tuple rows.

Internal server errors are logged server-side and return generic error messages;
filesystem paths, SQL errors, and exception text are not included in API or HTML
responses. Audit-sink failures are emitted through structured logging rather than
stdout. Reindex status is protected by the same permission gate as reindex
execution and its in-progress claim is lock-protected.
