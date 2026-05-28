# Licensing

Plexichat uses Ed25519-signed license files to gate premium features.

## License File Setup

Place a license file at one of these locations (checked in order):

1. `PLEXICHAT_LICENSE` env var as a file path
2. `PLEXICHAT_LICENSE` env var as a base64-encoded JSON string
3. `~/.plexichat/config/license`
4. `~/.plexichat/config/license.json`

## Permissions

License files must be readable by the server process. Ownership should be restricted to the server user.

## Hot-Swapping

The admin endpoint `POST /api/v1/admin/license/apply` applies a base64-encoded license **in-memory only**. It does **not** write to disk, so the change is lost on restart.

To make a permanent change:
1. Update the license file on disk
2. Call `POST /api/v1/admin/license/check` to reload it

## Verification

Use `GET /api/v1/admin/license/status` to inspect the current license state, including validity, expiry, instance ID, and active features.

To validate a license payload without applying it, use `POST /api/v1/admin/license/validate`.

## Obtaining a License

Contact sales@plexichat.com or visit https://plexichat.com.
