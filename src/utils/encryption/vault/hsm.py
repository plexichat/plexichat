"""HSM KEK/managed-crypto orchestration."""

from __future__ import annotations

import importlib
from typing import Any, Mapping

import utils.logger as logger

from .config import hardware_required, load_hsm_config, resolve_hsm_pin
from .errors import HsmCryptoError
from .hsm_envelope import parse_hsm_envelope
from . import hsm_pkcs11, hsm_pykcs11


def pkcs11_importable() -> bool:
    try:
        importlib.import_module("pkcs11")
        return True
    except ImportError:
        return False


def init_hsm(
    hsm_config: dict[str, Any] | None = None,
    *,
    require_hw: bool | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Initialize HSM availability.

    Returns (available, backend, config).
    """
    cfg = dict(hsm_config) if hsm_config is not None else dict(load_hsm_config())
    hw_required = hardware_required() if require_hw is None else require_hw
    if not bool(cfg.get("enabled", False)):
        try:
            if hw_required:
                logger.warning(
                    "HSM disabled while hardware_root_of_trust requires hardware; "
                    "TPM may still satisfy the policy if available"
                )
            else:
                logger.debug("HSM support disabled in configuration")
        except RuntimeError:
            pass
        return False, "", cfg

    pin = resolve_hsm_pin(cfg)
    if not cfg.get("library_path"):
        try:
            logger.warning("HSM enabled but missing required field: library_path")
        except RuntimeError:
            pass
        return False, "", cfg
    if cfg.get("slot_id") in (None, ""):
        try:
            logger.warning("HSM enabled but missing required field: slot_id")
        except RuntimeError:
            pass
        return False, "", cfg
    if not pin:
        try:
            logger.warning(
                "HSM enabled but no PIN available (set pin_env PLEXICHAT_HSM_PIN or pin_file)"
            )
        except RuntimeError:
            pass
        return False, "", cfg

    try:
        if hsm_pkcs11.probe_pkcs11(cfg):
            return True, "pkcs11", cfg
    except ImportError:
        pass
    except Exception as exc:
        try:
            logger.warning("HSM (pkcs11) probe failed: %s", exc)
        except RuntimeError:
            pass

    try:
        importlib.import_module("PyKCS11")
    except ImportError:
        try:
            logger.debug("PyKCS11 not installed, HSM support unavailable")
        except RuntimeError:
            pass
        return False, "", cfg
    except Exception as exc:
        try:
            logger.warning("HSM initialization failed: %s", exc)
        except RuntimeError:
            pass
        return False, "", cfg

    try:
        if hsm_pykcs11.probe_pykcs11(cfg):
            return True, "pykcs11", cfg
    except Exception as exc:
        try:
            logger.warning("HSM initialization failed: %s", exc)
        except RuntimeError:
            pass
    return False, "", cfg


def supports_hsm_crypto(hsm_available: bool, hsm_config: Mapping[str, Any]) -> bool:
    return bool(hsm_available and hsm_config.get("library_path"))


def get_hsm_key(
    hsm_available: bool, hsm_backend: str, hsm_config: Mapping[str, Any]
) -> bytes | None:
    if not hsm_available:
        return None
    pin = resolve_hsm_pin(hsm_config)
    if not pin:
        logger.error("HSM key retrieval failed: missing PIN")
        return None
    extractable = bool(hsm_config.get("key_extractable", True))
    allow_generate = bool(hsm_config.get("allow_generate", True))

    if hsm_backend in ("", "pkcs11"):
        try:
            importlib.import_module("pkcs11")
            return hsm_pkcs11.get_key(
                hsm_config,
                pin=pin,
                extractable=extractable,
                allow_generate=allow_generate,
            )
        except ImportError:
            pass
        except Exception as exc:
            logger.error("HSM (pkcs11) key retrieval failed: %s", exc)
            if hsm_backend == "pkcs11":
                raise
    try:
        return hsm_pykcs11.get_key(
            hsm_config, pin=pin, extractable=extractable, allow_generate=allow_generate
        )
    except Exception as exc:
        logger.error("HSM key retrieval/creation failed: %s", exc)
        raise


def hsm_encrypt(
    hsm_available: bool,
    hsm_backend: str,
    hsm_config: Mapping[str, Any],
    plaintext: bytes,
    aad: bytes | None,
) -> str:
    if not hsm_available:
        raise HsmCryptoError("HSM not available")
    pin = resolve_hsm_pin(hsm_config)
    if not pin:
        raise HsmCryptoError("HSM PIN unavailable")
    aad_bytes = aad or b""
    if hsm_backend == "pkcs11" or pkcs11_importable():
        try:
            return hsm_pkcs11.encrypt(
                hsm_config, pin=pin, plaintext=plaintext, aad=aad_bytes
            )
        except ImportError:
            pass
    return hsm_pykcs11.encrypt(hsm_config, pin=pin, plaintext=plaintext, aad=aad_bytes)


def hsm_decrypt(
    hsm_available: bool,
    hsm_backend: str,
    hsm_config: Mapping[str, Any],
    envelope: str,
    aad: bytes | None,
) -> bytes:
    if not hsm_available:
        raise HsmCryptoError("HSM not available")
    pin = resolve_hsm_pin(hsm_config)
    if not pin:
        raise HsmCryptoError("HSM PIN unavailable")
    blob = parse_hsm_envelope(envelope)
    iv, ct = blob[:12], blob[12:]
    aad_bytes = aad or b""
    if hsm_backend == "pkcs11" or pkcs11_importable():
        try:
            return hsm_pkcs11.decrypt(hsm_config, pin=pin, iv=iv, ct=ct, aad=aad_bytes)
        except ImportError:
            pass
    return hsm_pykcs11.decrypt(hsm_config, pin=pin, iv=iv, ct=ct, aad=aad_bytes)
