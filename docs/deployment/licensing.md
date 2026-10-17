# Licensing

Plexichat uses Ed25519-signed license files to gate premium features. Without a valid license, the server runs in free tier mode.

## How licenses work

- Each license is a JSON document signed with an Ed25519 private key held by Plexichat. The server verifies it with a hardcoded public key (`gBS50oixMLyikqv4Q/W3ME0cK0p5p1p2XS8KV8OKfTk=`).
- Premium features are disabled in free tier mode. Base features such as messaging remain available.
- A license of `type: "test"` or with `features.all: true` enables all features. Test licenses are restricted to localhost and display a banner in the client. Never use them in production.
- The server logs a warning on startup when no valid license is found.

## License file locations

The server checks these sources in order and uses the first valid license:

1. `PLEXICHAT_LICENSE` environment variable as a file path, if the file exists.
2. `PLEXICHAT_LICENSE` environment variable as a base64-encoded JSON license.
3. `~/.plexichat/config/license` — default location without extension.
4. `~/.plexichat/config/license.json` — default location with extension.
5. Free tier mode if none of the above contains a valid license.

Examples:

```bash
# As a file path
export PLEXICHAT_LICENSE=/etc/plexichat/license.json

# As base64 (for containerized deployments)
export PLEXICHAT_LICENSE=eyJ2ZXJzaW9uIjoiMS4wIiwiaW5zdGFuY2VfaWQiOiJwcm9kLTAxIiwuLi59
```

`DEFAULT_LICENSE_PATH` is `~/.plexichat/config/license` resolved against the current home directory at startup.

## File permissions

Restrict the license file to the server user:

```bash
chmod 600 ~/.plexichat/config/license.json
chown plexichat:plexichat ~/.plexichat/config/license.json
```

The file must be readable by the server process.

## Admin endpoints

All endpoints require an authenticated admin session. `POST /api/v1/admin/license/apply` and `POST /api/v1/admin/license/check` also require the `admin.license.manage` permission. Host restrictions may apply if `admin_ui.host_restriction` is enabled.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/admin/license/status` | Returns the current license state, including validity, expiry, instance ID, and active features. |
| `GET` | `/api/v1/admin/license/features` | Returns the feature matrix with enabled state, config, and limits. |
| `POST` | `/api/v1/admin/license/validate` | Validates a `{"license_key": "<base64>"}` payload without applying it. Returns `valid` and details. |
| `POST` | `/api/v1/admin/license/apply` | Applies a base64-encoded license in memory only. See Hot-swapping. |
| `POST` | `/api/v1/admin/license/check` | Reloads the license from disk or environment. Use after updating the file to make a change persistent. |

### Hot-swapping

`POST /api/v1/admin/license/apply` is transient. It updates the in-memory license and clears the capability cache, but it does not write to disk. The change is lost on restart.

To make a change permanent:

1. Update the license file on disk or update `PLEXICHAT_LICENSE`.
2. Call `POST /api/v1/admin/license/check` to reload.

Both endpoints are idempotent. If you send an empty or invalid `license_key`, the server returns the current state without error and audits the action as `noop`. In free tier mode, `/check` is also a no-op that returns `valid: true` and `free_tier: true`.

All actions are written to the admin audit log.

## Verification

Inspect the current license:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://your-server.com/api/v1/admin/license/status
```

To validate a license without applying it:

```bash
curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"license_key":"<base64>"}' \
  https://your-server.com/api/v1/admin/license/validate
```

## Obtaining a license

Contact `sales@plexichat.com` or visit `https://plexichat.com`.

## Related code

- `plexichat/src/utils/common_utils/utils/licensing/__init__.py:135` — `setup()`, load priority, and public key.
- `plexichat/src/utils/common_utils/utils/licensing/core.py` — `LicenseManager` verification and feature gating.
- `plexichat/src/api/routes/admin/licensing.py:21` — Admin endpoints under `/license`.
