"""Machine-local fallback KEK persistence."""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from functools import lru_cache
from pathlib import Path

import utils.logger as logger

from ..kek_validation import validate_kek
from .constants import MACHINE_KEY_DIR, MACHINE_KEY_FILENAME


def get_machine_key_path(kek_env_var: str) -> Path:
    """Get path for machine-local key file."""
    # Use dedicated key file ONLY if the dedicated env var is explicitly set.
    # Otherwise all keyrings share the single .machine_key fallback.
    if kek_env_var != "PLEXICHAT_SYSTEM_KEY" and os.environ.get(kek_env_var):
        key_name = kek_env_var.lower().replace("_key", "") + "_key"
        return MACHINE_KEY_DIR / f".{key_name}"
    return MACHINE_KEY_DIR / MACHINE_KEY_FILENAME


def _read_safe_existing_key_file(key_file: Path) -> bytes:
    """Read an existing machine key without a check/use (TOCTOU) race.

    The old loader called ``_is_safe_existing_key_file()`` (which ``stat``ed
    and ``chmod``ed the path) and then read the bytes in a *second* open via
    ``Path.read_bytes()``. A local attacker with write access to the key
    directory could swap the file for a symlink between those two operations:
    the safety check saw a legitimate 0600 regular file, but the read followed
    the freshly planted symlink and fed attacker-chosen 32 bytes into
    ``validate_kek``. That let an attacker pick the KEK protecting every
    keyring. The same race existed for the path-based ``chmod``.

    Open once with ``O_NOFOLLOW`` and validate the *opened descriptor* with
    ``fstat``/``fchmod``. The bytes returned are exactly the bytes that were
    checked; no path lookup is repeated, so there is nothing to race.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(key_file), flags)
    except OSError as exc:
        # O_NOFOLLOW reports a symlink swapped in after the caller's earlier
        # is_symlink()/exists() checks as ELOOP (EMLINK on some platforms).
        # Fail closed: never fall back to generating a key over that path.
        symlink_errnos = {
            code
            for code in (getattr(errno, "ELOOP", None), getattr(errno, "EMLINK", None))
            if code is not None
        }
        if exc.errno in symlink_errnos:
            raise RuntimeError(
                f"Unsafe machine key file at {key_file} (symlink swapped in)"
            ) from exc
        logger.error("Failed to open machine key file %s: %s", key_file, exc)
        raise

    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            logger.error("Refusing to read non-regular machine key file: %s", key_file)
            raise RuntimeError(
                f"Unsafe machine key file at {key_file} (not a regular file)"
            )
        if os.name != "nt":
            mode = stat.S_IMODE(st.st_mode)
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                logger.warning(
                    "Machine key file %s has permissive mode %o; restricting to 0600",
                    key_file,
                    mode,
                )
                try:
                    os.fchmod(fd, 0o600)
                except OSError as exc:
                    logger.error("Failed to restrict machine key permissions: %s", exc)
                    raise RuntimeError(
                        f"Unsafe machine key file at {key_file} (permissive mode)"
                    ) from exc
        with os.fdopen(fd, "rb") as handle:
            fd = -1  # ownership transferred to the file object
            return handle.read()
    finally:
        if fd >= 0:
            os.close(fd)


def _persist_machine_key(key_file: Path, new_key: bytes) -> None:
    """Persist a freshly generated KEK without writing through a symlink.

    The previous implementation opened ``key_file`` directly with
    ``O_WRONLY|O_CREAT|O_TRUNC``. The caller's ``Path.exists()`` guard follows
    symlinks, so a *dangling* symlink planted at the key path reported
    "missing" and the open wrote the new KEK straight through the symlink into
    an attacker-chosen file (truncating it). Writing to a unique temp file in
    the same directory and atomically replacing the final name fixes both that
    and the check/use race: ``os.replace`` replaces the directory entry
    itself, so a symlink swapped in mid-write is replaced, never followed.
    """
    key_file.parent.mkdir(parents=True, exist_ok=True)
    # is_symlink() does not follow the link, so it catches dangling symlinks
    # too (unlike exists()/stat()). Fail closed rather than redirecting the
    # KEK to whatever the symlink points at.
    if key_file.is_symlink():
        raise RuntimeError(f"Refusing to write machine key through symlink {key_file}")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{key_file.name}.", suffix=".tmp", dir=str(key_file.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass  # mkstemp already creates 0600 on POSIX
        with os.fdopen(fd, "wb") as handle:
            handle.write(new_key)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        # Covers platforms without fchmod (e.g. Windows ACLs).
        _restrict_machine_key_permissions(tmp_path)
        os.replace(tmp_path, key_file)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _restrict_machine_key_permissions(key_file: Path) -> None:
    if os.name == "nt":
        try:
            import getpass
            import subprocess

            current_user = os.environ.get("USERNAME") or getpass.getuser() or ""
            cmd = [
                "icacls",
                str(key_file),
                "/inheritance:r",
                "/grant:r",
                "*S-1-5-32-544:F",
            ]
            if current_user:
                cmd.extend(["/grant:r", f"{current_user}:F"])
            subprocess.run(cmd, capture_output=True, check=False)
        except Exception as exc:
            logger.warning("Failed to restrict Windows ACLs for %s: %s", key_file, exc)
        return
    try:
        os.chmod(key_file, 0o600)
    except (OSError, AttributeError) as exc:
        logger.warning("Failed to set permissions on %s: %s", key_file, exc)


def _load_machine_key_impl(key_path: str) -> bytes:
    """Load (or generate) the machine-local encryption key from disk.

    Cached by :func:`_load_machine_key_cached` so keyrings share a load.
    """
    key_file = Path(key_path)
    # is_symlink() is True even for a DANGLING symlink, unlike exists()/stat()
    # which follow it and report "missing". Refuse before any generation so a
    # planted symlink cannot redirect the new KEK to an attacker-chosen path.
    if key_file.is_symlink():
        logger.error("Refusing to use machine key symlink: %s", key_file)
        raise RuntimeError(
            f"Unsafe machine key file at {key_file} (symlink). "
            "Move it aside and configure a secure KEK source."
        )
    if key_file.exists():
        try:
            # Single open + fstat/fchmod: the safety checks and the bytes read
            # refer to the same file descriptor, so a symlink swapped in after
            # the is_symlink()/exists() checks cannot redirect the key we load.
            data = _read_safe_existing_key_file(key_file)
            if len(data) == 32:
                validated = validate_kek(
                    data, source="local", context=str(key_file), allow_bypass=True
                )
                logger.debug("Loaded machine-local encryption key from file")
                logger.debug(
                    "Machine key loaded (%s bytes) from %s - "
                    "this should appear only once per process per key file",
                    len(validated),
                    key_file,
                )
                return validated
            logger.error(
                "Machine key %s has invalid length %s bytes; generating a replacement",
                key_file,
                len(data),
            )
        except RuntimeError:
            # Unsafe file (symlink/non-regular/permissive we cannot fix): fail
            # closed. Never regenerate over a path an attacker controls.
            raise
        except Exception as exc:
            logger.error("Failed to read machine key: %s", exc)

    logger.warning(
        "CRITICAL SECURITY WARNING: Generating machine-local encryption key. "
        "This key is NOT hardware-bound and is less secure than HSM, TPM, or "
        "Environment Variable. For production deployments, set the appropriate "
        "PLEXICHAT_*_KEY environment variable or configure HSM/TPM."
    )
    new_key = os.urandom(32)
    try:
        _persist_machine_key(key_file, new_key)
    except Exception as exc:
        # Fail CLOSED. A freshly generated KEK that was never durably stored
        # cannot be used: the next process/start would mint a different one and
        # every keyring (and all data encrypted under it) would silently become
        # unreadable. Persistence failures are also security-relevant: the
        # writer refuses to follow a symlink and raises RuntimeError, so
        # swallowing that error would let a local attacker who wins the
        # check/use race force an ephemeral, unpersisted KEK on every start.
        # Surface the original error rather than returning the in-memory key.
        logger.critical(
            "Refusing to use machine-local encryption key: it could not be "
            "persisted to %s (%s). Without a durable KEK every restart would "
            "generate a new key and make all encrypted keyrings unreadable.",
            key_file,
            exc,
        )
        raise
    if key_file.exists():
        logger.info("Machine-local key saved to %s", key_file)

    validated = validate_kek(
        new_key, source="local", context=str(key_file), allow_bypass=True
    )
    logger.debug(
        "Machine key loaded (%s bytes) from %s - "
        "this should appear only once per process per key file",
        len(validated),
        key_file,
    )
    return validated


@lru_cache(maxsize=4)
def _load_machine_key_cached(key_path: str) -> bytes:
    """Cached wrapper around :func:`_load_machine_key_impl`."""
    return _load_machine_key_impl(key_path)
