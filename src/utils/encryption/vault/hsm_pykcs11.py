"""HSM PKCS#11 (PyKCS11 legacy) backend."""

from __future__ import annotations

import base64
import importlib
import os
from typing import Any, Mapping, cast

import utils.logger as logger

from .constants import HSM_ENVELOPE_PREFIX, HSM_IV_BYTES
from .errors import HsmCryptoError


def _import_pykcs11() -> Any:
    return cast(Any, importlib.import_module("PyKCS11")).PyKCS11


def probe_pykcs11(hsm_config: Mapping[str, Any]) -> bool:
    try:
        py = _import_pykcs11()
        pkcs11 = py.PyKCS11Lib()
        pkcs11.load(hsm_config["library_path"])
        slots = pkcs11.getSlotList(tokenPresent=True)
        if not slots:
            logger.warning("HSM enabled but no slots with tokens available")
            return False
        slot_id = int(str(hsm_config.get("slot_id", 0)), 0)
        if slot_id not in slots:
            logger.warning(
                "HSM configured slot %s not found in available slots: %s",
                slot_id,
                slots,
            )
            return False
        logger.info("HSM (PKCS#11) available on slot %s", slot_id)
        return True
    except Exception:
        return False


def get_key(
    hsm_config: Mapping[str, Any], *, pin: str, extractable: bool, allow_generate: bool
) -> bytes | None:
    py = _import_pykcs11()
    pkcs11 = py.PyKCS11Lib()
    pkcs11.load(hsm_config["library_path"])
    slot_id = int(str(hsm_config.get("slot_id", 0)), 0)
    key_label = str(hsm_config.get("key_label", "plexichat_kek"))
    session = pkcs11.openSession(slot_id, py.CKF_SERIAL_SESSION | py.CKF_RW_SESSION)
    session.login(pin)
    try:
        template = [
            (py.CKA_LABEL, key_label),
            (py.CKA_CLASS, py.CKO_SECRET_KEY),
            (py.CKA_KEY_TYPE, py.CKK_AES),
            (py.CKA_VALUE_LEN, 32),
        ]
        objects = session.findObjects(template)
        if objects:
            key_obj = objects[0]
            attrs = session.getAttributeValue(
                key_obj, [py.CKA_EXTRACTABLE, py.CKA_VALUE]
            )
            extractable_attr, value_attr = attrs[0], attrs[1]
            if extractable_attr == py.CK_FALSE:
                logger.critical(
                    "HSM key '%s' is marked CKA_EXTRACTABLE=FALSE. Refusing to "
                    "extract CKA_VALUE into process memory; callers must move to "
                    "HSM-managed cryptographic operations.",
                    key_label,
                )
                return None
            logger.info("Retrieved existing key '%s' from HSM", key_label)
            return bytes(value_attr)

        if not allow_generate:
            raise HsmCryptoError(
                f"HSM key '{key_label}' not found and allow_generate=False"
            )
        logger.info("Key '%s' not found in HSM, creating new key", key_label)
        new_key = os.urandom(32)
        key_template = [
            (py.CKA_CLASS, py.CKO_SECRET_KEY),
            (py.CKA_KEY_TYPE, py.CKK_AES),
            (py.CKA_VALUE_LEN, 32),
            (py.CKA_LABEL, key_label),
            (py.CKA_ENCRYPT, py.CK_TRUE),
            (py.CKA_DECRYPT, py.CK_TRUE),
            (py.CKA_EXTRACTABLE, py.CK_TRUE if extractable else py.CK_FALSE),
            (py.CKA_PRIVATE, py.CK_TRUE),
            (py.CKA_VALUE, new_key if extractable else os.urandom(32)),
        ]
        session.createObject(key_template)
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
    finally:
        try:
            session.logout()
        except Exception as exc:
            # The key was already created; teardown failures must not mask it.
            logger.debug(f"HSM logout after key creation failed: {exc}")
        try:
            session.closeSession()
        except Exception as exc:
            logger.debug(f"HSM session close after key creation failed: {exc}")


def encrypt(
    hsm_config: Mapping[str, Any], *, pin: str, plaintext: bytes, aad: bytes
) -> str:
    iv = os.urandom(HSM_IV_BYTES)
    blob = _crypt(hsm_config, pin=pin, data=plaintext, iv=iv, aad=aad, encrypt=True)
    return HSM_ENVELOPE_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt(
    hsm_config: Mapping[str, Any], *, pin: str, iv: bytes, ct: bytes, aad: bytes
) -> bytes:
    return _crypt(hsm_config, pin=pin, data=ct, iv=iv, aad=aad, encrypt=False)


def _crypt(
    hsm_config: Mapping[str, Any],
    *,
    pin: str,
    data: bytes,
    iv: bytes,
    aad: bytes,
    encrypt: bool,
) -> bytes:
    py = _import_pykcs11()
    pkcs11 = py.PyKCS11Lib()
    pkcs11.load(hsm_config["library_path"])
    slot_id = int(str(hsm_config.get("slot_id", 0)), 0)
    key_label = str(hsm_config.get("key_label", "plexichat_kek"))
    session = pkcs11.openSession(slot_id, py.CKF_SERIAL_SESSION | py.CKF_RW_SESSION)
    try:
        session.login(pin)
        try:
            objs = session.findObjects(
                [(py.CKA_LABEL, key_label), (py.CKA_CLASS, py.CKO_SECRET_KEY)]
            )
            if not objs:
                raise HsmCryptoError(f"HSM key '{key_label}' not found")
            mech = gcm_mechanism(py, iv, aad)
            try:
                if encrypt:
                    return iv + bytes(session.encrypt(objs[0], data, mech))
                return bytes(session.decrypt(objs[0], data, mech))
            except Exception as exc:
                op = "encrypt" if encrypt else "decrypt"
                raise HsmCryptoError(f"HSM {op} failed: {exc}") from exc
        finally:
            try:
                session.logout()
            except Exception as exc:
                logger.debug(f"HSM logout after crypt operation failed: {exc}")
    finally:
        try:
            session.closeSession()
        except Exception as exc:
            logger.debug(f"HSM session close after crypt operation failed: {exc}")


def gcm_mechanism(py: Any, iv: bytes, aad: bytes) -> Any:
    ckm = getattr(py, "CKM_AES_GCM", None)
    if ckm is None:
        raise HsmCryptoError("PyKCS11 build lacks CKM_AES_GCM")
    last_error: Exception | None = None
    for param in (
        (bytes(iv), bytes(aad), 128),
        {"iv": bytes(iv), "aad": bytes(aad), "tag_bits": 128},
        bytes(iv) + bytes(aad),
    ):
        try:
            return py.Mechanism(ckm, param)
        except Exception as exc:
            # Builds differ in which parameter shape they accept; keep the last
            # failure so the error raised below names a real cause.
            last_error = exc
    raise HsmCryptoError("could not build CKM_AES_GCM mechanism param") from last_error
