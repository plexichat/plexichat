"""Machine-local fallback KEK persistence."""

from __future__ import annotations

import os
import stat
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


def _is_safe_existing_key_file(key_file: Path) -> bool:
    try:
        if key_file.is_symlink():
            logger.error("Refusing to read machine key symlink: %s", key_file)
            return False
        st = key_file.stat()
        if not stat.S_ISREG(st.st_mode):
            logger.error("Refusing to read non-regular machine key file: %s", key_file)
            return False
        if os.name != "nt":
            mode = stat.S_IMODE(st.st_mode)
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                logger.warning(
                    "Machine key file %s has permissive mode %o; restricting to 0600",
                    key_file,
                    mode,
                )
                try:
                    os.chmod(key_file, 0o600)
                except OSError as exc:
                    logger.error("Failed to restrict machine key permissions: %s", exc)
                    return False
    except OSError as exc:
        logger.error("Failed to inspect machine key file %s: %s", key_file, exc)
        return False
    return True


def _persist_machine_key(key_file: Path, new_key: bytes) -> None:
    key_file.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    try:
        fd = os.open(str(key_file), flags, 0o600)
    except OSError:
        key_file.write_bytes(new_key)
        _restrict_machine_key_permissions(key_file)
        return
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(new_key)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
    except Exception:
        try:
            key_file.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _restrict_machine_key_permissions(key_file)


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
    if key_file.exists():
        if _is_safe_existing_key_file(key_file):
            try:
                data = key_file.read_bytes()
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
            except Exception as exc:
                logger.error("Failed to read machine key: %s", exc)
        else:
            raise RuntimeError(
                f"Unsafe machine key file at {key_file} (symlink/non-regular/permissive). "
                "Move it aside and configure a secure KEK source."
            )

    logger.warning(
        "CRITICAL SECURITY WARNING: Generating machine-local encryption key. "
        "This key is NOT hardware-bound and is less secure than HSM, TPM, or "
        "Environment Variable. For production deployments, set the appropriate "
        "PLEXICHAT_*_KEY environment variable or configure HSM/TPM."
    )
    new_key = os.urandom(32)
    try:
        _persist_machine_key(key_file, new_key)
        if key_file.exists():
            logger.info("Machine-local key saved to %s", key_file)
    except Exception as exc:
        logger.error("Failed to persist machine key: %s", exc)

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
