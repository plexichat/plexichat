"""HSM PKCS#11 (pkcs11 package) backend."""

from __future__ import annotations

import base64
import importlib
import os
from typing import Any, Mapping

import utils.logger as logger

from .constants import HSM_ENVELOPE_PREFIX, HSM_IV_BYTES
from .errors import HsmCryptoError


def _import_pkcs11() -> Any:
    return importlib.import_module("pkcs11")


def probe_pkcs11(hsm_config: Mapping[str, Any]) -> bool:
    try:
        pkcs11_mod = _import_pkcs11()
        lib = pkcs11_mod.lib(str(hsm_config["library_path"]))
        tokens = list(lib.get_tokens())
        if not tokens:
            logger.warning("HSM enabled but no tokens available")
            return False
        logger.info("HSM (PKCS#11 via pkcs11) available")
        return True
    except Exception:
        return False


def resolve_token(pkcs11_mod: Any, lib: Any, hsm_config: Mapping[str, Any]) -> Any:
    slot_id = hsm_config.get("slot_id", 0)
    token_label = hsm_config.get("token_label") or None
    try:
        tokens = list(
            lib.get_tokens(token_label=token_label) if token_label else lib.get_tokens()
        )
    except TypeError:
        tokens = list(lib.get_tokens())
    if not tokens:
        raise HsmCryptoError("no PKCS#11 tokens available")
    if slot_id not in (None, ""):
        try:
            wanted = int(str(slot_id), 0) if isinstance(slot_id, str) else int(slot_id)
        except (TypeError, ValueError):
            wanted = -1
        for tok in tokens:
            if _slot_matches(tok, wanted):
                return tok
    return tokens[0]


def get_key(
    hsm_config: Mapping[str, Any], *, pin: str, extractable: bool, allow_generate: bool
) -> bytes | None:
    pkcs11_mod = _import_pkcs11()
    lib = pkcs11_mod.lib(str(hsm_config["library_path"]))
    key_label = str(hsm_config.get("key_label", "plexichat_kek"))
    token = resolve_token(pkcs11_mod, lib, hsm_config)
    with token.open(user_pin=pin, rw=True) as session:
        existing = list(
            session.get_objects({"CKA_LABEL": key_label, "CKA_CLASS": "CKO_SECRET_KEY"})
        )
        if existing:
            key = existing[0]
            try:
                value = key["CKA_VALUE"]
            except Exception:
                value = None
            if value is None:
                logger.critical(
                    "HSM key '%s' is non-extractable. Refusing to export it; "
                    "use hsm_encrypt()/hsm_decrypt() instead.",
                    key_label,
                )
                return None
            logger.info("Retrieved existing key '%s' from HSM", key_label)
            return bytes(value)
        if not allow_generate:
            raise HsmCryptoError(
                f"HSM key '{key_label}' not found and allow_generate=False"
            )
        new_key = os.urandom(32)
        _create_aes_key(session, pkcs11_mod, key_label, new_key, extractable)
        if not extractable:
            logger.info(
                "Created non-extractable key '%s' in HSM; use hsm_encrypt()/hsm_decrypt().",
                key_label,
            )
            return None
        logger.warning(
            "Created new extractable-key '%s' in HSM. Consider migrating to "
            "key_extractable=False + hsm_encrypt()/hsm_decrypt() for stronger guarantees.",
            key_label,
        )
        return new_key


def _slot_matches(token: Any, wanted: int) -> bool:
    """Return whether a PKCS#11 token reports the wanted slot id."""
    try:
        return int(getattr(token, "slot_id", -1)) == wanted
    except (TypeError, ValueError):
        return False


def _create_aes_key(
    session: Any,
    pkcs11_mod: Any,
    key_label: str,
    new_key: bytes,
    extractable: bool,
) -> None:
    key_type = getattr(pkcs11_mod, "KeyType")
    mechanism = getattr(pkcs11_mod, "Mechanism")
    template: dict[str, Any] = {
        "CKA_LABEL": key_label,
        "CKA_PRIVATE": True,
        "CKA_TOKEN": True,
        "CKA_SENSITIVE": True,
        "CKA_EXTRACTABLE": extractable,
        "CKA_ENCRYPT": True,
        "CKA_DECRYPT": True,
    }
    if extractable:
        # Attribute assignment cannot fail; the old try/except hid nothing.
        template["CKA_VALUE"] = new_key
    session.generate_key(
        key_type.AES, 256, mechanism=mechanism.AES_KEY_GEN, template=template
    )


def encrypt(
    hsm_config: Mapping[str, Any], *, pin: str, plaintext: bytes, aad: bytes
) -> str:
    iv = os.urandom(HSM_IV_BYTES)
    blob = _encrypt_bytes(hsm_config, pin=pin, plaintext=plaintext, iv=iv, aad=aad)
    return HSM_ENVELOPE_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt(
    hsm_config: Mapping[str, Any], *, pin: str, iv: bytes, ct: bytes, aad: bytes
) -> bytes:
    return _decrypt_bytes(hsm_config, pin=pin, iv=iv, ct=ct, aad=aad)


def _open_session(hsm_config: Mapping[str, Any], pin: str) -> tuple[Any, Any]:
    pkcs11_mod = _import_pkcs11()
    lib = pkcs11_mod.lib(str(hsm_config["library_path"]))
    token = resolve_token(pkcs11_mod, lib, hsm_config)
    return pkcs11_mod, token.open(user_pin=pin, rw=True)


def _find_key(session: Any, key_label: str) -> Any:
    objs = list(
        session.get_objects({"CKA_LABEL": key_label, "CKA_CLASS": "CKO_SECRET_KEY"})
    )
    if not objs:
        raise HsmCryptoError(f"HSM key '{key_label}' not found")
    return objs[0]


def _gcm_mechanism(pkcs11_mod: Any, iv: bytes, aad: bytes) -> Any:
    mechanism = getattr(pkcs11_mod, "Mechanism")
    try:
        return mechanism.AES_GCM(iv, aad, 128)
    except TypeError:
        try:
            return mechanism.AES_GCM(iv, 128)
        except Exception as exc:
            raise HsmCryptoError(f"HSM AES-GCM mechanism unavailable: {exc}") from exc


def _encrypt_bytes(
    hsm_config: Mapping[str, Any], *, pin: str, plaintext: bytes, iv: bytes, aad: bytes
) -> bytes:
    pkcs11_mod, session_ctx = _open_session(hsm_config, pin)
    key_label = str(hsm_config.get("key_label", "plexichat_kek"))
    with session_ctx as session:
        key = _find_key(session, key_label)
        mech = _gcm_mechanism(pkcs11_mod, iv, aad)
        try:
            ct = bytes(session.encrypt(key, plaintext, mechanism=mech))
        except Exception as exc:
            raise HsmCryptoError(f"HSM encrypt failed: {exc}") from exc
        return iv + ct


def _decrypt_bytes(
    hsm_config: Mapping[str, Any], *, pin: str, iv: bytes, ct: bytes, aad: bytes
) -> bytes:
    pkcs11_mod, session_ctx = _open_session(hsm_config, pin)
    key_label = str(hsm_config.get("key_label", "plexichat_kek"))
    with session_ctx as session:
        key = _find_key(session, key_label)
        mech = _gcm_mechanism(pkcs11_mod, iv, aad)
        try:
            return bytes(session.decrypt(key, ct, mechanism=mech))
        except Exception as exc:
            raise HsmCryptoError(f"HSM decrypt failed: {exc}") from exc
