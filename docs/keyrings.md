# Keyrings and KEK management

Plexichat encrypts sensitive data at rest with three encrypted keyrings. Each keyring is encrypted with a Key Encryption Key (KEK). The KEK never leaves the vault; the keyring files contain only AES-GCM ciphertext.

## What is stored

Plexichat stores three keyrings in `~/.plexichat/data/`:

| File | Purpose | Default KEK env var |
| --- | --- | --- |
| `system_keyring.json` | Admin TOTP, API tokens, and other system material | `PLEXICHAT_SYSTEM_KEY` |
| `message_keyring.json` | Encrypted message-at-rest keys | `PLEXICHAT_MESSAGE_KEY` |
| `file_keyring.json` | Avatars, attachments, and other media keys | `PLEXICHAT_MEDIA_KEY` |

The keyring implementation is in `src/utils/encryption/core/keyring.py:42` and the default KEK selection is in `src/utils/encryption/core/keyring.py:94`.

If the KEK changes without re-encrypting the keyring, the application fails closed rather than decrypting with the wrong key.

## KEK source order

The vault resolves the KEK in the following order (`src/utils/encryption/vault.py:19`):

1. **Dedicated environment variable** — `PLEXICHAT_SYSTEM_KEY`, `PLEXICHAT_MESSAGE_KEY`, or `PLEXICHAT_MEDIA_KEY`.
2. **HSM (PKCS#11)** — When `encryption.hsm.enabled` is `true` and the library, slot, and PIN are configured.
3. **TPM 2.0** — When `tpm2-pytss` is installed and a TPM is present.
4. **Machine-local fallback file** — `~/.plexichat/data/.machine_key` (or a per-keyring file such as `.message_key` when the dedicated env var path is used). The fallback is acceptable only for development or recovery.

In production, use dedicated environment variables or hardware-backed KEKs. The machine-local fallback is not suitable for production.

### Environment variables

*   `PLEXICHAT_SYSTEM_KEY` — 32-byte key as 64-character hex or Base64. The vault tries hex first, then Base64 (`src/utils/encryption/vault.py:192`).
*   `PLEXICHAT_MESSAGE_KEY` — Dedicated KEK for `message_keyring.json`.
*   `PLEXICHAT_MEDIA_KEY` — Dedicated KEK for `file_keyring.json`.

Each variable must decode to exactly 32 bytes. The vault logs the variable name, never the value.

## Startup behavior

The server validates keyrings during startup so a KEK mismatch is visible immediately. The keyring loader tries the dedicated KEK first and, for non-system keyrings, falls back to `PLEXICHAT_SYSTEM_KEY` only as a recovery path for pre-migration keyrings (`src/utils/encryption/core/keyring.py:211`). After migration, each keyring validates with its dedicated KEK.

When `authentication.encryption.require_secure_source` is enabled and the runtime falls back to the machine-local file, startup fails. This prevents the server from booting with a weaker root of trust than the deployment expects.

You can inspect the current KEK source at runtime:

```python
from src.utils.encryption.vault import HardwareVault

vault = HardwareVault(kek_env_var="PLEXICHAT_MESSAGE_KEY")
print(vault.get_source())           # "env", "hsm", "tpm", or "local"
print(vault.is_using_secure_source())  # True when source is env, hsm, or tpm
```

The source detection logic is in `src/utils/encryption/vault.py:404` and `src/utils/encryption/core/keyring.py:104`.

## Migration workflow

Use the built-in migration mode in `main.py` when you change KEKs or move from a shared KEK to dedicated KEKs:

```bash
# Validate all keyrings with current KEKs
python main.py migrate-kek --kek-validate --kek-all

# Migrate the system keyring in place (same env var, re-encrypt)
python main.py migrate-kek --kek-keyring system_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_SYSTEM_KEY

# Split the message keyring from the shared system KEK to its dedicated KEK
python main.py migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY

# Split the file keyring from the shared system KEK to its dedicated KEK
python main.py migrate-kek --kek-keyring file_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MEDIA_KEY

# Dry run without writing
python main.py migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY --kek-dry-run

# Roll back a migration
python main.py migrate-kek --kek-rollback --kek-keyring message_keyring.json
```

Follow this sequence:

1.  Back up the keyring files, or rely on the daily restic `backup` container snapshot. Run `docker exec plexichat-backup-1 /scripts/backup.sh run` immediately before you migrate.
2.  Validate the current KEK and the target KEK.
3.  Re-encrypt the keyring under the new KEK.
4.  Run `python main.py migrate-kek --kek-validate --kek-all`.
5.  Restart the application and confirm that the new keyring loads without errors.

If validation fails, restore the backup before you retry. Don't overwrite a working keyring to test whether it works.

## Key rotation

Each keyring supports multiple key versions for rotation. Use the keyring API to rotate the data-encryption key without changing the KEK:

```python
from pathlib import Path
from src.utils.encryption.core.keyring import Keyring

keyring = Keyring(Path.home() / ".plexichat" / "data" / "message_keyring.json")
new_version = keyring.rotate()  # Generates a new 256-bit key
print(f"Rotated to version {new_version}")
```

The rotation logic is in `src/utils/encryption/core/keyring.py:388`. The current version and all previous versions remain readable for decrypting existing data. Wrap and unwrap helpers use the `ENC:{version}:{base64(nonce||ct||tag)}` envelope (`src/utils/encryption/core/keyring.py:404`).

## Recovery

When the server fails to decrypt a keyring, the most common causes are:

*   The wrong KEK is loaded in the environment.
*   The keyring was rotated on another host.
*   A deployment was rolled back without restoring the matching keyring files.

To recover:

1.  Stop the service.
2.  Restore the last known good keyring backup to `~/.plexichat/data/`.
3.  Confirm the expected KEK environment variables on the host.
4.  Run `python main.py migrate-kek --kek-validate --kek-all`.
5.  Restart only after validation succeeds.

The keyring loader logs a critical message with the expected file paths when decryption fails (`src/utils/encryption/core/keyring.py:261`) and invalidates cached keys.

## Logging

When a keyring mismatch occurs, Plexichat writes the failure to `latest.log` and to the dated session log in `~/.plexichat/logs/`. This gives you a short-path view of the current failure and an archive trail for later review.

## Related configuration

| Key | Default | Description |
| --- | --- | --- |
| `encryption.hsm.enabled` | `false` | Enable HSM (PKCS#11) support. |
| `encryption.hsm.library_path` | `/usr/lib/softhsm/libsofthsm2.so` | Path to the PKCS#11 library. |
| `encryption.hsm.slot_id` | `0` | HSM slot ID. |
| `encryption.hsm.key_label` | `plexichat_kek` | Label of the key object in the HSM. |
| `encryption.key_rotation_days` | `180` | Recommended interval for data-key rotation. |
| `authentication.encryption.require_secure_source` | `false` | When `true`, the server refuses to start with a machine-local KEK. |
