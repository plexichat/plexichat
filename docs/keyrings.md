# Keyrings and KEK Migration

Plexichat uses encrypted keyrings for the secrets that keep the platform running. This page collects the operational rules for the new keyring layout and the KEK migration flow that accompanies it.

## What Is Stored

Plexichat keeps three separate keyrings in `~/.plexichat/data/`:

- `system_keyring.json` for admin TOTP, API tokens, and other system material
- `message_keyring.json` for encrypted message-at-rest keys
- `file_keyring.json` for avatars, attachments, and other media keys

Each keyring is encrypted with a Key Encryption Key, or KEK. If the KEK changes without re-encrypting the keyring, the application will fail closed rather than silently decrypting with the wrong key.

## KEK Source Order

Plexichat resolves KEKs in this order:

1. Dedicated environment variable
2. HSM
3. TPM 2.0
4. Machine-local fallback file

The dedicated environment variables are:

- `PLEXICHAT_SYSTEM_KEY`
- `PLEXICHAT_MESSAGE_KEY`
- `PLEXICHAT_MEDIA_KEY`

In production, prefer dedicated environment variables or hardware-backed KEKs. The machine-local fallback is only acceptable for development or recovery workflows.

## Startup Behavior

The server validates keyrings during startup so KEK mismatches are visible immediately. If `authentication.encryption.require_secure_source` is enabled and the runtime falls back to the local machine file, startup fails.

That failure mode is intentional. It prevents the server from silently booting with a weaker root of trust than the deployment expects.

Runtime keyring loading uses the keyring filename to select the default dedicated KEK:

- `system_keyring.json` loads with `PLEXICHAT_SYSTEM_KEY`
- `message_keyring.json` loads with `PLEXICHAT_MESSAGE_KEY`
- `file_keyring.json` loads with `PLEXICHAT_MEDIA_KEY`

For compatibility with pre-migration keyrings, non-system keyrings may retry `PLEXICHAT_SYSTEM_KEY` only as a recovery fallback. After migration, each keyring should validate with its dedicated KEK.

## Migration Workflow

Use the built-in migration mode in `main.py` when changing KEKs or moving from a shared KEK to dedicated keyring KEKs:

```bash
python main.py migrate-kek --kek-validate --kek-all
python main.py migrate-kek --kek-keyring system_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_SYSTEM_KEY
python main.py migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY
python main.py migrate-kek --kek-keyring file_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MEDIA_KEY
python main.py migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY --kek-dry-run
python main.py migrate-kek --kek-rollback --kek-keyring message_keyring.json
```

The migration path is:

1. Back up the keyring files
2. Validate the current KEK and the target KEK
3. Re-encrypt the keyring under the new KEK
4. Run `python main.py migrate-kek --kek-validate --kek-all`
5. Re-open the application and confirm the new keyring loads cleanly

If validation fails, restore the backup before retrying. Do not overwrite a working keyring just to "see if it works."

## Recovery Notes

If the server starts failing on keyring decryption, the most common causes are:

- The wrong KEK is loaded in the environment
- A keyring was rotated on another host
- A deployment was rolled back without restoring the matching keyring files

The safe recovery sequence is:

1. Stop the service
2. Restore the last known good keyring backup
3. Confirm the expected KEK env vars on the host
4. Run validation
5. Restart only after validation succeeds

## Logging Tie-In

When a keyring mismatch occurs, Plexichat writes the failure to `latest.log` and to the dated session log in `/root/.plexichat/logs/` on CT 120. That gives operators a short-path view of the current failure while preserving an archive trail for later review.
