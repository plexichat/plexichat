"""Hardware-rooted vault for master key management."""

from __future__ import annotations

import os
from typing import Any, Optional

import utils.logger as logger

from .config import hardware_required, require_hardware_source
from .env_keys import decode_env_key
from .hsm import (
    get_hsm_key as _hsm_get_key,
    hsm_decrypt as _hsm_decrypt,
    hsm_encrypt as _hsm_encrypt,
    init_hsm,
    pkcs11_importable,
    supports_hsm_crypto,
)
from .hsm_envelope import parse_hsm_envelope
from .machine_key import get_machine_key_path
from .models import HardwareStatus, SourcePolicy
from .tpm import (
    get_tpm_key as _tpm_get_key,
    init_tpm,
    marshal_tpm_public,
    tpm_object_name,
)


class HardwareVault:
    """Manages the Root of Trust (ROT) for the application."""

    def __init__(self, kek_env_var: Optional[str] = None):
        self._master_key: Optional[bytes] = None
        self._source: str = "unknown"
        self._kek_env_var = kek_env_var or "PLEXICHAT_SYSTEM_KEY"
        self._tpm_available = False
        self._tpm_enabled = True
        self._tpm_config: dict[str, Any] = {}
        self._hsm_available = False
        self._hsm_backend: str = ""
        self._hsm_config: dict[str, Any] = {}

        self._init_hsm()
        self._init_tpm()

    def _tpm_config_dict(self) -> dict[str, Any]:
        try:
            import utils.config as config

            return dict(config.get("encryption", {}).get("tpm", {}) or {})
        except Exception:
            return {}

    def _init_tpm(self) -> None:
        cfg = self._tpm_config_dict()
        self._tpm_available, self._tpm_enabled, self._tpm_config = init_tpm(cfg)

    def _init_hsm(self) -> None:
        self._hsm_available, self._hsm_backend, self._hsm_config = init_hsm()

    def get_kek(self) -> bytes:
        if self._master_key:
            return self._master_key

        require_hw = hardware_required()

        env_key = os.environ.get(self._kek_env_var)
        if env_key:
            key = self._decode_env_key(env_key)
            if (
                require_hw
                and self._kek_env_var == "PLEXICHAT_SYSTEM_KEY"
                and not (self._hsm_available or self._tpm_available)
            ):
                # Dedicated HSM/TPM root required; ignore system env KEK.
                pass
            else:
                self._master_key = key
                self._source = "env"
                logger.info("Using environment-provided KEK from %s", self._kek_env_var)
                return self._master_key

        if self._hsm_available:
            try:
                hsm_key = self._get_hsm_key()
                if hsm_key:
                    from ..kek_validation import validate_kek

                    self._master_key = validate_kek(
                        hsm_key, source="hsm", context=self._kek_env_var
                    )
                    self._source = "hsm"
                    logger.info("Using HSM (PKCS#11) derived encryption key")
                    return self._master_key
            except Exception as exc:
                logger.error("HSM key retrieval failed: %s", exc)
                if require_hw and not self._tpm_available:
                    raise

        if self._tpm_available:
            try:
                tpm_key = self._get_tpm_key()
                if tpm_key:
                    from ..kek_validation import validate_kek

                    self._master_key = validate_kek(
                        tpm_key, source="tpm", context=self._kek_env_var
                    )
                    self._source = "tpm"
                    logger.info("Using TPM-derived hardware encryption key")
                    return self._master_key
            except Exception as exc:
                logger.error("TPM key retrieval failed: %s", exc)
                if require_hw:
                    raise

        require_hardware_source("local")

        key_file = self._get_machine_key_path()
        local_key = self._load_machine_key_cached(str(key_file))
        self._master_key = local_key
        self._source = "local"
        return self._master_key

    @staticmethod
    def _load_machine_key_cached(key_path: str) -> bytes:
        import sys
        from typing import cast

        for name in ("src.utils.encryption.vault", "utils.encryption.vault"):
            pkg = sys.modules.get(name)
            if pkg is not None:
                loader = getattr(pkg, "_load_machine_key_cached", None)
                if callable(loader):
                    return cast(bytes, loader(key_path))
        from .machine_key import _load_machine_key_cached as loader

        return loader(key_path)

    def _get_machine_key_path(self) -> Any:
        return get_machine_key_path(self._kek_env_var)

    def _decode_env_key(self, env_value: str) -> bytes:
        return decode_env_key(env_value, context=self._kek_env_var)

    def _hsm_pin(self) -> str:
        from .config import resolve_hsm_pin

        return resolve_hsm_pin(self._hsm_config)

    def _hsm_key_extractable(self) -> bool:
        return bool(self._hsm_config.get("key_extractable", True))

    def _hsm_allow_generate(self) -> bool:
        return bool(self._hsm_config.get("allow_generate", True))

    def _get_hsm_key(self) -> Optional[bytes]:
        return _hsm_get_key(self._hsm_available, self._hsm_backend, self._hsm_config)

    def _get_hsm_key_pkcs11(self, pin: str) -> Optional[bytes]:
        from .hsm_pkcs11 import get_key

        return get_key(
            self._hsm_config,
            pin=pin,
            extractable=self._hsm_key_extractable(),
            allow_generate=self._hsm_allow_generate(),
        )

    def _get_hsm_key_pykcs11(self, pin: str) -> Optional[bytes]:
        from .hsm_pykcs11 import get_key

        return get_key(
            self._hsm_config,
            pin=pin,
            extractable=self._hsm_key_extractable(),
            allow_generate=self._hsm_allow_generate(),
        )

    @staticmethod
    def _pkcs11_create_aes_key(
        session: Any, pkcs11_mod: Any, key_label: str, new_key: bytes, extractable: bool
    ) -> None:
        from .hsm_pkcs11 import _create_aes_key

        _create_aes_key(session, pkcs11_mod, key_label, new_key, extractable)

    @staticmethod
    def _pkcs11_importable() -> bool:
        return pkcs11_importable()

    def _resolve_pkcs11_token(self, pkcs11_mod: Any, lib: Any) -> Any:
        from .hsm_pkcs11 import resolve_token

        return resolve_token(pkcs11_mod, lib, self._hsm_config)

    def supports_hsm_crypto(self) -> bool:
        return supports_hsm_crypto(self._hsm_available, self._hsm_config)

    def hsm_encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> str:
        return _hsm_encrypt(
            self._hsm_available, self._hsm_backend, self._hsm_config, plaintext, aad
        )

    def hsm_decrypt(self, envelope: str, aad: Optional[bytes] = None) -> bytes:
        return _hsm_decrypt(
            self._hsm_available, self._hsm_backend, self._hsm_config, envelope, aad
        )

    def _hsm_open_pkcs11_session(self, pin: str) -> tuple[Any, Any]:
        from .hsm_pkcs11 import _open_session

        return _open_session(self._hsm_config, pin)

    def _hsm_find_pkcs11_key(self, session: Any, key_label: str) -> Any:
        from .hsm_pkcs11 import _find_key

        return _find_key(session, key_label)

    def _hsm_encrypt_pkcs11(
        self, pin: str, plaintext: bytes, iv: bytes, aad: bytes
    ) -> bytes:
        from .hsm_pkcs11 import _encrypt_bytes

        return _encrypt_bytes(
            self._hsm_config, pin=pin, plaintext=plaintext, iv=iv, aad=aad
        )

    def _hsm_decrypt_pkcs11(self, pin: str, iv: bytes, ct: bytes, aad: bytes) -> bytes:
        from .hsm_pkcs11 import _decrypt_bytes

        return _decrypt_bytes(self._hsm_config, pin=pin, iv=iv, ct=ct, aad=aad)

    def _hsm_encrypt_pykcs11(
        self, pin: str, plaintext: bytes, iv: bytes, aad: bytes
    ) -> bytes:
        from .hsm_pykcs11 import _crypt

        return _crypt(
            self._hsm_config, pin=pin, data=plaintext, iv=iv, aad=aad, encrypt=True
        )

    def _hsm_decrypt_pykcs11(self, pin: str, iv: bytes, ct: bytes, aad: bytes) -> bytes:
        from .hsm_pykcs11 import _crypt

        return _crypt(self._hsm_config, pin=pin, data=ct, iv=iv, aad=aad, encrypt=False)

    @staticmethod
    def _pykcs11_gcm_mechanism(py: Any, iv: bytes, aad: bytes) -> Any:
        from .hsm_pykcs11 import gcm_mechanism

        return gcm_mechanism(py, iv, aad)

    def _get_tpm_key(self) -> Optional[bytes]:
        if not self._tpm_available:
            return None
        return _tpm_get_key(self._tpm_config)

    @staticmethod
    def _tpm_marshal_public(out_public: Any) -> bytes:
        return marshal_tpm_public(out_public)

    @staticmethod
    def _tpm_object_name(ctx: Any, handle: Any, out_public: Any) -> bytes:
        return tpm_object_name(ctx, handle, out_public)

    def get_hardware_status(self) -> HardwareStatus:
        return {
            "kek_source": self._source,
            "hsm_available": self._hsm_available,
            "hsm_backend": self._hsm_backend or None,
            "hsm_managed_crypto": self.supports_hsm_crypto(),
            "tpm_enabled": self._tpm_enabled,
            "tpm_available": self._tpm_available,
        }

    def is_using_secure_source(self) -> bool:
        if not self._master_key:
            self.get_kek()
        return self._source in ("env", "hsm", "tpm")

    def is_using_hardware_source(self) -> bool:
        if not self._master_key:
            self.get_kek()
        return self._source in ("hsm", "tpm")

    def get_source(self) -> str:
        if not self._master_key:
            self.get_kek()
        return self._source

    def describe_source_policy(self) -> SourcePolicy:
        secure_source = self.is_using_secure_source()
        hardware_rooted = self.is_using_hardware_source()
        base = self.get_hardware_status()
        status: SourcePolicy = {
            "kek_source": base["kek_source"],
            "hsm_available": base["hsm_available"],
            "hsm_backend": base["hsm_backend"],
            "hsm_managed_crypto": base["hsm_managed_crypto"],
            "tpm_enabled": base["tpm_enabled"],
            "tpm_available": base["tpm_available"],
            "secure_source": secure_source,
            "hardware_rooted": hardware_rooted,
        }
        return status


def _parse_hsm_envelope(envelope: str) -> bytes:
    return parse_hsm_envelope(envelope)
