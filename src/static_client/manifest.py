"""
Static client manifest generator and verifier.

Maintains a signed manifest of file hashes for the installed client,
verified on every startup to detect tampering, bit rot, or key migration.

The manifest (``manifest.json``) contains SHA256 hashes of every installed
file except ``config.js`` (server-generated). It is signed with an
HMAC-SHA256 using a key HKDF-derived from the server's KEK.

Architecture
------------
* ``install_zip_at`` → ``_fetch_and_install`` writes manifest.json + .hmac
  (+ a key-epoch marker recording the signing-key fingerprint)
* ``ensure_active``  → on startup, verifies HMAC then re-checks every file
* On HMAC mismatch, the install is treated as UNTRUSTED and re-downloaded,
  EXCEPT for the narrow key-migration case: the stored key epoch differs
  from the current key AND every on-disk file still matches the signed
  hash list — only then is the manifest re-signed under the new key
* On file hash mismatch (tamper/rot), a re-download is triggered
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Dict, Optional, Set

import utils.logger as logger

_MANIFEST_FILE = "manifest.json"
_HMAC_FILE = "manifest.json.hmac"
_EPOCH_FILE = "manifest.json.epoch"
EXCLUDED_FILES: Set[str] = {
    "config.js",
    _MANIFEST_FILE,
    _HMAC_FILE,
    _EPOCH_FILE,
}
# Back-compat alias used by existing callers.
_EXCLUDED_FILES = EXCLUDED_FILES
_CHUNK = 64 * 1024

# Verification outcomes returned by :func:`verify_manifest_detailed`.
VERIFY_OK = "ok"
VERIFY_HMAC_MISMATCH = "hmac_mismatch"
VERIFY_HASH_MISMATCH = "hash_mismatch"
VERIFY_MISSING = "missing"
VERIFY_ERROR = "error"


def _hash_file(path: Path) -> str:
    """Compute SHA256 hex digest of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            chunk = fp.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def key_epoch(signing_key: bytes) -> str:
    """Return a short fingerprint of *signing_key* (the ``key epoch``).

    Stored next to the manifest so a later HMAC failure can be attributed
    to a key rotation rather than tampering. The fingerprint is a one-way
    hash and leaks nothing about the key itself.
    """
    return hashlib.sha256(b"epoch:" + signing_key).hexdigest()[:16]


def read_key_epoch(install_dir: Path) -> Optional[str]:
    """Return the stored key epoch for an install, or ``None``."""
    epoch_path = install_dir / _EPOCH_FILE
    try:
        value = epoch_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def generate_manifest(
    install_dir: Path, exclude: Optional[Set[str]] = None
) -> Dict[str, str]:
    """Build a dict mapping relative file paths to SHA256 hex digests.

    Skips files in the ``_EXCLUDED_FILES`` set (config.js, manifest files)
    plus any caller-supplied *exclude* paths.
    """
    excluded = _EXCLUDED_FILES | (exclude or set())
    files: Dict[str, str] = {}
    for entry in sorted(
        install_dir.rglob("*"), key=lambda p: p.relative_to(install_dir)
    ):
        if not entry.is_file():
            continue
        rel = str(entry.relative_to(install_dir)).replace("\\", "/")
        if rel in excluded:
            continue
        files[rel] = _hash_file(entry)
    return files


def write_manifest(
    install_dir: Path, files: Dict[str, str], signing_key: bytes
) -> None:
    """Atomically write ``manifest.json``, its HMAC and the key-epoch marker."""
    manifest = {"files": files}
    manifest_bytes = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    hmac_value = hmac.new(signing_key, manifest_bytes, hashlib.sha256).hexdigest()

    (install_dir / _MANIFEST_FILE).write_bytes(manifest_bytes)
    (install_dir / _HMAC_FILE).write_text(hmac_value + "\n", encoding="utf-8")
    (install_dir / _EPOCH_FILE).write_text(
        key_epoch(signing_key) + "\n", encoding="utf-8"
    )


def verify_manifest_detailed(install_dir: Path, signing_key: bytes) -> str:
    """Verify HMAC + all file hashes in the manifest.

    Returns one of ``VERIFY_OK``, ``VERIFY_HMAC_MISMATCH``,
    ``VERIFY_HASH_MISMATCH``, ``VERIFY_MISSING`` or ``VERIFY_ERROR`` so
    callers can distinguish a key migration (HMAC mismatch under an
    unchanged file set) from tampering before deciding to re-sign.
    """
    manifest_path = install_dir / _MANIFEST_FILE
    hmac_path = install_dir / _HMAC_FILE

    if not manifest_path.is_file() or not hmac_path.is_file():
        return VERIFY_MISSING

    try:
        manifest_bytes = manifest_path.read_bytes()
        stored_hmac = hmac_path.read_text(encoding="utf-8").strip()
        expected_hmac = hmac.new(
            signing_key, manifest_bytes, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(stored_hmac, expected_hmac):
            logger.warning(
                "static_client: manifest HMAC mismatch "
                "(possible key migration or tampering)"
            )
            return VERIFY_HMAC_MISMATCH

        manifest = json.loads(manifest_bytes)
        files: Dict[str, str] = manifest.get("files", {})
        for rel_path, expected_hash in files.items():
            # Forward-slash relative paths resolve correctly on every platform
            # via pathlib. The previous ``rel_path.replace("/", "\\")`` turned
            # the separator into a literal backslash on POSIX, so nested files
            # (assets/*, js/*, .vite/*) could never verify.
            file_path = install_dir / rel_path
            if not file_path.is_file():
                logger.warning("static_client: manifest file missing: %s", rel_path)
                return VERIFY_MISSING
            actual = _hash_file(file_path)
            if not hmac.compare_digest(actual, expected_hash):
                logger.warning("static_client: manifest hash mismatch for %s", rel_path)
                return VERIFY_HASH_MISMATCH

        logger.info("static_client: manifest OK (%d files verified)", len(files))
        return VERIFY_OK

    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("static_client: manifest read/parse error: %s", exc)
        return VERIFY_ERROR


def files_match_manifest(install_dir: Path) -> bool:
    """Check every on-disk file against the hashes recorded in the manifest.

    This deliberately does NOT verify the manifest's HMAC; it is used only
    as a secondary integrity cross-check when deciding whether an HMAC
    failure is a benign key migration (files intact) or tampering.
    """
    manifest_path = install_dir / _MANIFEST_FILE
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (OSError, json.JSONDecodeError):
        return False
    for rel_path, expected_hash in manifest.get("files", {}).items():
        file_path = install_dir / rel_path
        if not file_path.is_file():
            return False
        if not hmac.compare_digest(_hash_file(file_path), expected_hash):
            return False
    return True


def verify_manifest(install_dir: Path, signing_key: bytes) -> bool:
    """Verify HMAC + all file hashes in the manifest.

    Returns ``True`` if everything checks out.
    Returns ``False`` on HMAC mismatch, file hash mismatch, or missing files.
    """
    return verify_manifest_detailed(install_dir, signing_key) == VERIFY_OK


def regenerate_manifest(install_dir: Path, signing_key: bytes) -> bool:
    """Regenerate the manifest from on-disk files and sign it.

    This handles key migration: the files are intact but the HMAC key
    changed, so the manifest needs re-signing with the new key.

    Returns ``True`` on success, ``False`` if no files found or I/O error.
    """
    try:
        files = generate_manifest(install_dir)
        if not files:
            logger.warning(
                "static_client: no files found to generate manifest in %s",
                install_dir,
            )
            return False
        write_manifest(install_dir, files, signing_key)
        logger.info(
            "static_client: regenerated manifest (%d files, new key)",
            len(files),
        )
        return True
    except OSError as exc:
        logger.warning("static_client: failed to regenerate manifest: %s", exc)
        return False


_signing_key_cache: Optional[bytes] = None


def get_signing_key() -> Optional[bytes]:
    """Derive an HMAC signing key from the server's KEK via HKDF.

    Returns ``None`` if the KEK is unavailable (e.g. no encryption
    configured), in which case manifest verification is skipped.
    """
    global _signing_key_cache
    if _signing_key_cache is not None:
        return _signing_key_cache
    try:
        from src.utils.encryption.vault import vault
        from src.utils.encryption.hkdf import derive_key

        kek = vault.get_kek()
        _signing_key_cache = derive_key(
            kek,
            salt=b"plexichat-static-client-v1",
            info=b"static-file-hmac",
            length=32,
        )
        return _signing_key_cache
    except Exception as exc:
        logger.warning(
            "static_client: cannot derive signing key (skip manifest): %s",
            exc,
        )
        return None


__all__ = [
    "generate_manifest",
    "write_manifest",
    "verify_manifest",
    "verify_manifest_detailed",
    "files_match_manifest",
    "regenerate_manifest",
    "get_signing_key",
    "key_epoch",
    "read_key_epoch",
    "_MANIFEST_FILE",
    "_HMAC_FILE",
    "_EPOCH_FILE",
]
