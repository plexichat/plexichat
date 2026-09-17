"""
Hardware-rooted vault for master key management.
Supports TPM 2.0 (via tpm2-pytss), HSM (via PKCS#11), and environment-derived KEK.
"""

import base64
import importlib
import os
from functools import lru_cache
from typing import Optional, Dict, Any, Tuple, cast
from pathlib import Path

import utils.logger as logger


class HsmCryptoError(RuntimeError):
    """Raised when HSM-managed crypto (non-extractable key ops) fails."""


HSM_ENVELOPE_PREFIX = "HSM1:"


def tpm_bound_kek(
    public_bytes: bytes,
    name_bytes: bytes,
    *,
    label: str = "PLEXICHAT_KEK_V2",
    salt: str = "plexichat:tpm-kek:v2",
) -> bytes:
    """Derive a TPM-bound 32-byte KEK via HKDF.

    Binds the output to the TPM's primary public area + object name, so
    different TPMs (or different primary templates) yield different KEKs.
    Testable without hardware: pure function of its inputs.
    """
    from .hkdf import derive_key

    if not public_bytes or not name_bytes:
        raise ValueError("tpm_bound_kek needs non-empty public_bytes and name_bytes")
    return derive_key(
        public_bytes + name_bytes,
        salt=salt.encode("utf-8"),
        info=label.encode("utf-8"),
        length=32,
    )


def _parse_hsm_envelope(envelope: str) -> bytes:
    if not envelope.startswith(HSM_ENVELOPE_PREFIX):
        raise HsmCryptoError("not an HSM envelope")
    try:
        return base64.b64decode(envelope[len(HSM_ENVELOPE_PREFIX) :])
    except Exception as exc:
        raise HsmCryptoError(f"malformed HSM envelope: {exc}") from exc


class HardwareVault:
    """
    Manages the Root of Trust (ROT) for the application.
    Supports HSM (PKCS#11), TPM 2.0, and environment-derived KEK.

    KEK priority order:
    1. Environment variable (PLEXICHAT_SYSTEM_KEY or keyring-specific KEK)
    2. HSM (PKCS#11)
    3. TPM 2.0
    4. Machine-local file (fallback)
    """

    def __init__(self, kek_env_var: Optional[str] = None):
        self._master_key: Optional[bytes] = None
        self._source: str = "unknown"
        self._kek_env_var = kek_env_var or "PLEXICHAT_SYSTEM_KEY"
        self._tpm_available = False
        self._tpm_enabled = True
        self._tpm_config: Dict[str, Any] = {}
        self._hsm_available = False
        self._hsm_backend: str = ""
        self._hsm_config: Dict[str, Any] = {}

        # Initialize hardware sources
        self._init_hsm()
        self._init_tpm()

    def _tpm_config_dict(self) -> Dict[str, Any]:
        try:
            import utils.config as config

            return dict(config.get("encryption", {}).get("tpm", {}) or {})
        except Exception:
            return {}

    def _init_tpm(self):
        """Try to initialize TPM 2.0 connection.

        Honors ``encryption.tpm.enabled`` (default True for backwards
        compat: previously TPM was auto-detected with no config knob).
        The body is inlined (no nested def) so the outer method actually runs.
        """
        self._tpm_config = self._tpm_config_dict()
        self._tpm_enabled = bool(self._tpm_config.get("enabled", True))
        if not self._tpm_enabled:
            logger.debug("TPM 2.0 disabled in configuration")
            return
        try:
            tpm2_pytss = cast(Any, importlib.import_module("tpm2_pytss"))
            if tpm2_pytss is None:
                raise ImportError("tpm2_pytss is None")
            ESYS_Context = tpm2_pytss.ESYS_Context

            with ESYS_Context() as ctx:
                # Check for TPM presence without deep interaction yet
                cap = ctx.get_capability(0, 0x00000001, 1)  # TPM_CAP_PROPERTIES
                if cap:
                    self._tpm_available = True
                    logger.info("TPM 2.0 hardware detected and available")
        except (ImportError, Exception):
            logger.debug("TPM 2.0 not available or tpm2-pytss not installed")

    def _init_hsm(self):
        """Try to initialize HSM (PKCS#11) connection."""
        try:
            import utils.config as config

            hsm_config = config.get("encryption", {}).get("hsm", {})
            if not hsm_config.get("enabled", False):
                try:
                    logger.debug("HSM support disabled in configuration")
                except RuntimeError:
                    pass  # Logger not configured yet
                return

            # PIN hygiene: resolve from pin_env / pin_file / ${VAR} expansion
            # (see pepper.resolve_pin). A literal `pin` is only for SoftHSM
            # dev setups.
            try:
                from .pepper import resolve_pin as _resolve_pin

                pin = _resolve_pin(hsm_config)
            except Exception:
                pin = str(hsm_config.get("pin", ""))
            # Check for required HSM configuration
            if not hsm_config.get("library_path"):
                try:
                    logger.warning(
                        "HSM enabled but missing required field: library_path"
                    )
                except RuntimeError:
                    pass
                return
            if hsm_config.get("slot_id") in (None, ""):
                try:
                    logger.warning("HSM enabled but missing required field: slot_id")
                except RuntimeError:
                    pass
                return
            if not pin:
                try:
                    logger.warning(
                        "HSM enabled but no PIN available (set pin_env "
                        "PLEXICHAT_HSM_PIN or pin_file)"
                    )
                except RuntimeError:
                    pass
                return

            # Prefer the maintained `pkcs11` package; fall back to PyKCS11.
            try:
                pkcs11_mod = importlib.import_module("pkcs11")
                lib = pkcs11_mod.lib(str(hsm_config["library_path"]))
                tokens = list(lib.get_tokens())
                if not tokens:
                    try:
                        logger.warning("HSM enabled but no tokens available")
                    except RuntimeError:
                        pass
                    return
                self._hsm_available = True
                self._hsm_backend = "pkcs11"
                self._hsm_config = dict(hsm_config)
                try:
                    logger.info("HSM (PKCS#11 via pkcs11) available")
                except RuntimeError:
                    pass
                return
            except ImportError:
                pass

            # Try to import PyKCS11 (legacy fallback)
            PyKCS11 = cast(Any, importlib.import_module("PyKCS11")).PyKCS11

            # Test HSM connection
            pkcs11 = PyKCS11.PyKCS11Lib()
            pkcs11.load(hsm_config["library_path"])

            # Get slot info
            slots = pkcs11.getSlotList(tokenPresent=True)
            if not slots:
                try:
                    logger.warning("HSM enabled but no slots with tokens available")
                except RuntimeError:
                    pass  # Logger not configured yet
                return

            # Verify the configured slot exists
            slot_id = int(hsm_config["slot_id"])
            if slot_id not in slots:
                try:
                    logger.warning(
                        f"HSM configured slot {slot_id} not found in available slots: {slots}"
                    )
                except RuntimeError:
                    pass  # Logger not configured yet
                return

            self._hsm_available = True
            self._hsm_backend = "pykcs11"
            self._hsm_config = dict(hsm_config)
            try:
                logger.info(f"HSM (PKCS#11) available on slot {slot_id}")
            except RuntimeError:
                pass  # Logger not configured yet

        except ImportError:
            try:
                logger.debug("PyKCS11 not installed, HSM support unavailable")
            except RuntimeError:
                pass  # Logger not configured yet
        except Exception as e:
            try:
                logger.warning(f"HSM initialization failed: {e}")
            except RuntimeError:
                pass  # Logger not configured yet

    def get_kek(self) -> bytes:
        """
        Get the Key Encryption Key (KEK).
        Derived from keyring-specific env var, HSM, TPM, or machine-local file.

        Prioritizes:
        1. Environment variable (keyring-specific KEK)
        2. HSM (PKCS#11)
        3. TPM 2.0
        4. Machine-local file (fallback)
        """
        if self._master_key:
            return self._master_key

        # 1. Check for dedicated environment key (Highest Priority)
        env_key = os.environ.get(self._kek_env_var)
        if env_key:
            self._master_key = self._decode_env_key(env_key)
            self._source = "env"
            logger.info(f"Using environment-provided KEK from {self._kek_env_var}")
            return self._master_key

        # 2. Try HSM (PKCS#11)
        if self._hsm_available:
            try:
                self._master_key = self._get_hsm_key()
                if self._master_key:
                    self._source = "hsm"
                    logger.info("Using HSM (PKCS#11) derived encryption key")
                    return self._master_key
            except Exception as e:
                logger.error(f"HSM key retrieval failed: {e}")

        # 3. Try TPM 2.0
        if self._tpm_available:
            try:
                self._master_key = self._get_tpm_key()
                if self._master_key:
                    self._source = "tpm"
                    logger.info("Using TPM-derived hardware encryption key")
                    return self._master_key
            except Exception as e:
                logger.error(f"TPM key retrieval failed: {e}")

        # 4. Single-machine mode: load (or generate) a machine-local key.
        # The actual disk read is cached at module level so the three keyrings
        # (system, file, message) share a single load.
        key_file = self._get_machine_key_path()
        self._master_key = _load_machine_key_cached(str(key_file))
        self._source = "local"
        return self._master_key

    def _get_machine_key_path(self) -> Path:
        """Get path for machine-local key file."""
        # Use dedicated key file ONLY if the dedicated env var is explicitly set
        # Otherwise all keyrings share the single .machine_key fallback
        if self._kek_env_var != "PLEXICHAT_SYSTEM_KEY" and os.environ.get(
            self._kek_env_var
        ):
            key_name = self._kek_env_var.lower().replace("_key", "") + "_key"
            return Path.home() / ".plexichat" / "data" / f".{key_name}"
        return Path.home() / ".plexichat" / "data" / ".machine_key"

    def _decode_env_key(self, env_value: str) -> bytes:
        """Decode environment variable key (supports hex and Base64).

        Tries hex first (the standard Plexichat production format: 64-char hex = 32 bytes),
        then falls back to Base64. This order is important because a 64-char hex string
        decodes to 48 bytes in Base64 — it passes the length check incorrectly when
        Base64 is tried first.
        """
        import base64

        # Try hex first (standard Plexichat production format: 64 hex chars = 32 bytes)
        try:
            key = bytes.fromhex(env_value)
            if len(key) == 32:
                return key
            logger.debug(
                f"Environment variable {self._kek_env_var} decoded as hex but yielded {len(key)} bytes (expected 32)"
            )
        except Exception:
            pass

        # Try Base64 (alternative format)
        try:
            key = base64.b64decode(env_value)
            if len(key) == 32:
                return key
            logger.debug(
                f"Environment variable {self._kek_env_var} decoded as Base64 but yielded {len(key)} bytes (expected 32)"
            )
        except Exception:
            pass

        raise ValueError(
            f"Environment variable {self._kek_env_var} must be a 32-byte key (hex or Base64 encoded)"
        )

    def _hsm_pin(self) -> str:
        try:
            from .pepper import resolve_pin as _resolve_pin

            return _resolve_pin(self._hsm_config)
        except Exception:
            return str(self._hsm_config.get("pin", ""))

    def _hsm_key_extractable(self) -> bool:
        return bool(self._hsm_config.get("key_extractable", True))

    def _hsm_allow_generate(self) -> bool:
        return bool(self._hsm_config.get("allow_generate", True))

    def _get_hsm_key(self) -> Optional[bytes]:
        """Retrieve or create the HSM KEK, honouring CKA_EXTRACTABLE.

        Dispatches to the modern ``pkcs11`` backend first, then the legacy
        ``PyKCS11`` fallback (mirroring :meth:`_init_hsm`). Previously this
        method only spoke PyKCS11, so hosts with just ``pkcs11`` installed
        probed "available" yet always failed at retrieval time.
        """
        pin = self._hsm_pin()
        if not pin:
            logger.error("HSM key retrieval failed: missing PIN")
            return None
        if self._hsm_backend in ("", "pkcs11"):
            try:
                importlib.import_module("pkcs11")
                return self._get_hsm_key_pkcs11(pin)
            except ImportError:
                pass
            except Exception as e:
                logger.error(f"HSM (pkcs11) key retrieval failed: {e}")
                if self._hsm_backend == "pkcs11":
                    raise
        try:
            return self._get_hsm_key_pykcs11(pin)
        except Exception as e:
            logger.error(f"HSM key retrieval/creation failed: {e}")
            raise

    def _resolve_pkcs11_token(self, pkcs11_mod: Any, lib: Any) -> Any:
        slot_id = self._hsm_config.get("slot_id", 0)
        token_label = self._hsm_config.get("token_label") or None
        try:
            tokens = list(
                lib.get_tokens(token_label=token_label)
                if token_label
                else lib.get_tokens()
            )
        except TypeError:
            tokens = list(lib.get_tokens())
        if not tokens:
            raise HsmCryptoError("no PKCS#11 tokens available")
        if slot_id not in (None, ""):
            try:
                wanted = int(slot_id)
                for tok in tokens:
                    try:
                        if int(getattr(tok, "slot_id", -1)) == wanted:
                            return tok
                    except Exception:
                        continue
            except (TypeError, ValueError):
                pass
        return tokens[0]

    def _get_hsm_key_pkcs11(self, pin: str) -> Optional[bytes]:
        pkcs11_mod = importlib.import_module("pkcs11")
        lib = pkcs11_mod.lib(str(self._hsm_config["library_path"]))
        key_label = str(self._hsm_config.get("key_label", "plexichat_kek"))
        token = self._resolve_pkcs11_token(pkcs11_mod, lib)
        with token.open(user_pin=pin, rw=True) as session:
            existing = list(
                session.get_objects(
                    {"CKA_LABEL": key_label, "CKA_CLASS": "CKO_SECRET_KEY"}
                )
            )
            if existing:
                key = existing[0]
                try:
                    value = key["CKA_VALUE"]
                except Exception:
                    value = None
                if value is None:
                    logger.critical(
                        f"HSM key '{key_label}' is non-extractable. Refusing to "
                        "export it; use hsm_encrypt()/hsm_decrypt() instead."
                    )
                    return None
                logger.info(f"Retrieved existing key '{key_label}' from HSM")
                return bytes(value)
            if not self._hsm_allow_generate():
                raise HsmCryptoError(
                    f"HSM key '{key_label}' not found and allow_generate=False"
                )
            new_key = os.urandom(32)
            self._pkcs11_create_aes_key(
                session, pkcs11_mod, key_label, new_key, self._hsm_key_extractable()
            )
            if not self._hsm_key_extractable():
                logger.info(
                    f"Created non-extractable key '{key_label}' in HSM; "
                    "use hsm_encrypt()/hsm_decrypt()."
                )
                return None
            logger.warning(
                f"Created new extractable-key '{key_label}' in HSM. "
                "Consider migrating to key_extractable=False + "
                "hsm_encrypt()/hsm_decrypt() for stronger guarantees."
            )
            return new_key

    @staticmethod
    def _pkcs11_create_aes_key(
        session: Any,
        pkcs11_mod: Any,
        key_label: str,
        new_key: bytes,
        extractable: bool,
    ) -> None:
        KeyType = getattr(pkcs11_mod, "KeyType")
        Mechanism = getattr(pkcs11_mod, "Mechanism")
        template: Dict[str, Any] = {
            "CKA_LABEL": key_label,
            "CKA_PRIVATE": True,
            "CKA_TOKEN": True,
            "CKA_SENSITIVE": True,
            "CKA_EXTRACTABLE": extractable,
            "CKA_ENCRYPT": True,
            "CKA_DECRYPT": True,
        }
        if extractable:
            try:
                template["CKA_VALUE"] = new_key
            except Exception:
                pass
        session.generate_key(
            KeyType.AES, 256, mechanism=Mechanism.AES_KEY_GEN, template=template
        )

    # -- HSM-managed crypto (non-extractable keys) ---------------------
    def supports_hsm_crypto(self) -> bool:
        """True when HSM-managed encrypt/decrypt can be attempted."""
        return bool(self._hsm_available and self._hsm_config.get("library_path"))

    def hsm_encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> str:
        """Encrypt inside the HSM with AES-GCM; key never leaves the token.

        Returns an ``HSM1:<base64(iv||ciphertext+tag)>`` envelope.
        """
        if not self._hsm_available:
            raise HsmCryptoError("HSM not available")
        pin = self._hsm_pin()
        if not pin:
            raise HsmCryptoError("HSM PIN unavailable")
        iv = os.urandom(12)
        if self._hsm_backend == "pkcs11" or self._pkcs11_importable():
            try:
                blob = self._hsm_encrypt_pkcs11(pin, plaintext, iv, aad or b"")
                return HSM_ENVELOPE_PREFIX + base64.b64encode(blob).decode("ascii")
            except ImportError:
                pass
        blob = self._hsm_encrypt_pykcs11(pin, plaintext, iv, aad or b"")
        return HSM_ENVELOPE_PREFIX + base64.b64encode(blob).decode("ascii")

    def hsm_decrypt(self, envelope: str, aad: Optional[bytes] = None) -> bytes:
        """Reverse of :meth:`hsm_encrypt` (AES-GCM inside the token)."""
        if not self._hsm_available:
            raise HsmCryptoError("HSM not available")
        pin = self._hsm_pin()
        if not pin:
            raise HsmCryptoError("HSM PIN unavailable")
        blob = _parse_hsm_envelope(envelope)
        if len(blob) < 12 + 16:
            raise HsmCryptoError("HSM envelope too short")
        iv, ct = blob[:12], blob[12:]
        if self._hsm_backend == "pkcs11" or self._pkcs11_importable():
            try:
                return self._hsm_decrypt_pkcs11(pin, iv, ct, aad or b"")
            except ImportError:
                pass
        return self._hsm_decrypt_pykcs11(pin, iv, ct, aad or b"")

    @staticmethod
    def _pkcs11_importable() -> bool:
        try:
            importlib.import_module("pkcs11")
            return True
        except ImportError:
            return False

    def _hsm_open_pkcs11_session(self, pin: str) -> Tuple[Any, Any]:
        pkcs11_mod = importlib.import_module("pkcs11")
        lib = pkcs11_mod.lib(str(self._hsm_config["library_path"]))
        token = self._resolve_pkcs11_token(pkcs11_mod, lib)
        return pkcs11_mod, token.open(user_pin=pin, rw=True)

    def _hsm_find_pkcs11_key(self, session: Any, key_label: str) -> Any:
        objs = list(
            session.get_objects({"CKA_LABEL": key_label, "CKA_CLASS": "CKO_SECRET_KEY"})
        )
        if not objs:
            raise HsmCryptoError(f"HSM key '{key_label}' not found")
        return objs[0]

    def _hsm_encrypt_pkcs11(
        self, pin: str, plaintext: bytes, iv: bytes, aad: bytes
    ) -> bytes:
        pkcs11_mod, session_ctx = self._hsm_open_pkcs11_session(pin)
        Mechanism = getattr(pkcs11_mod, "Mechanism")
        key_label = str(self._hsm_config.get("key_label", "plexichat_kek"))
        with session_ctx as session:
            key = self._hsm_find_pkcs11_key(session, key_label)
            try:
                mechanism = Mechanism.AES_GCM(iv, aad, 128)
            except TypeError:
                try:
                    mechanism = Mechanism.AES_GCM(iv, 128)
                except Exception as exc:
                    raise HsmCryptoError(
                        f"HSM AES-GCM mechanism unavailable: {exc}"
                    ) from exc
            try:
                ct = bytes(session.encrypt(key, plaintext, mechanism=mechanism))
            except Exception as exc:
                raise HsmCryptoError(f"HSM encrypt failed: {exc}") from exc
            return iv + ct

    def _hsm_decrypt_pkcs11(self, pin: str, iv: bytes, ct: bytes, aad: bytes) -> bytes:
        pkcs11_mod, session_ctx = self._hsm_open_pkcs11_session(pin)
        Mechanism = getattr(pkcs11_mod, "Mechanism")
        key_label = str(self._hsm_config.get("key_label", "plexichat_kek"))
        with session_ctx as session:
            key = self._hsm_find_pkcs11_key(session, key_label)
            try:
                mechanism = Mechanism.AES_GCM(iv, aad, 128)
            except TypeError:
                try:
                    mechanism = Mechanism.AES_GCM(iv, 128)
                except Exception as exc:
                    raise HsmCryptoError(
                        f"HSM AES-GCM mechanism unavailable: {exc}"
                    ) from exc
            try:
                return bytes(session.decrypt(key, ct, mechanism=mechanism))
            except Exception as exc:
                raise HsmCryptoError(f"HSM decrypt failed: {exc}") from exc

    def _hsm_encrypt_pykcs11(
        self, pin: str, plaintext: bytes, iv: bytes, aad: bytes
    ) -> bytes:
        PyKCS11 = cast(Any, importlib.import_module("PyKCS11")).PyKCS11
        pkcs11 = PyKCS11.PyKCS11Lib()
        pkcs11.load(self._hsm_config["library_path"])
        slot_id = int(self._hsm_config["slot_id"])
        key_label = str(self._hsm_config.get("key_label", "plexichat_kek"))
        session = pkcs11.openSession(
            slot_id, PyKCS11.CKF_SERIAL_SESSION | PyKCS11.CKF_RW_SESSION
        )
        try:
            session.login(pin)
            try:
                objs = session.findObjects(
                    [
                        (PyKCS11.CKA_LABEL, key_label),
                        (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
                    ]
                )
                if not objs:
                    raise HsmCryptoError(f"HSM key '{key_label}' not found")
                mech = self._pykcs11_gcm_mechanism(PyKCS11, iv, aad)
                try:
                    ct = bytes(session.encrypt(objs[0], plaintext, mech))
                except Exception as exc:
                    raise HsmCryptoError(f"HSM encrypt failed: {exc}") from exc
                return iv + ct
            finally:
                try:
                    session.logout()
                except Exception:
                    pass
        finally:
            try:
                session.closeSession()
            except Exception:
                pass

    def _hsm_decrypt_pykcs11(self, pin: str, iv: bytes, ct: bytes, aad: bytes) -> bytes:
        PyKCS11 = cast(Any, importlib.import_module("PyKCS11")).PyKCS11
        pkcs11 = PyKCS11.PyKCS11Lib()
        pkcs11.load(self._hsm_config["library_path"])
        slot_id = int(self._hsm_config["slot_id"])
        key_label = str(self._hsm_config.get("key_label", "plexichat_kek"))
        session = pkcs11.openSession(
            slot_id, PyKCS11.CKF_SERIAL_SESSION | PyKCS11.CKF_RW_SESSION
        )
        try:
            session.login(pin)
            try:
                objs = session.findObjects(
                    [
                        (PyKCS11.CKA_LABEL, key_label),
                        (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
                    ]
                )
                if not objs:
                    raise HsmCryptoError(f"HSM key '{key_label}' not found")
                mech = self._pykcs11_gcm_mechanism(PyKCS11, iv, aad)
                try:
                    return bytes(session.decrypt(objs[0], ct, mech))
                except Exception as exc:
                    raise HsmCryptoError(f"HSM decrypt failed: {exc}") from exc
            finally:
                try:
                    session.logout()
                except Exception:
                    pass
        finally:
            try:
                session.closeSession()
            except Exception:
                pass

    @staticmethod
    def _pykcs11_gcm_mechanism(PyKCS11: Any, iv: bytes, aad: bytes) -> Any:
        ckm = getattr(PyKCS11, "CKM_AES_GCM", None)
        if ckm is None:
            raise HsmCryptoError("PyKCS11 build lacks CKM_AES_GCM")
        for param in (
            (bytes(iv), bytes(aad), 128),
            {"iv": bytes(iv), "aad": bytes(aad), "tag_bits": 128},
            bytes(iv) + bytes(aad),
        ):
            try:
                return PyKCS11.Mechanism(ckm, param)
            except Exception:
                continue
        raise HsmCryptoError("could not build CKM_AES_GCM mechanism param")

    def _get_hsm_key_pykcs11(self, pin: str) -> Optional[bytes]:
        """Legacy PyKCS11 retrieval path (extractable keys only)."""
        try:
            PyKCS11 = cast(Any, importlib.import_module("PyKCS11")).PyKCS11

            pkcs11 = PyKCS11.PyKCS11Lib()
            pkcs11.load(self._hsm_config["library_path"])

            slot_id = int(self._hsm_config["slot_id"])
            key_label = self._hsm_config.get("key_label", "plexichat_kek")

            # Open session and login
            session = pkcs11.openSession(
                slot_id, PyKCS11.CKF_SERIAL_SESSION | PyKCS11.CKF_RW_SESSION
            )
            session.login(pin)

            try:
                # Try to find existing key by label
                template = [
                    (PyKCS11.CKA_LABEL, key_label),
                    (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
                    (PyKCS11.CKA_KEY_TYPE, PyKCS11.CKK_AES),
                    (PyKCS11.CKA_VALUE_LEN, 32),
                ]

                objects = session.findObjects(template)

                if objects:
                    # Key exists, extract it
                    key_obj = objects[0]
                    # SECURITY: respect CKA_EXTRACTABLE so the
                    # ``non-extractable`` flag actually means what
                    # it says. Previously we extracted CKA_VALUE for
                    # any key without checking the flag, which
                    # silently negated the CKA_EXTRACTABLE=FALSE
                    # guarantee the operator relied on.
                    attrs = session.getAttributeValue(
                        key_obj,
                        [PyKCS11.CKA_EXTRACTABLE, PyKCS11.CKA_VALUE],
                    )
                    extractable_attr, value_attr = attrs[0], attrs[1]
                    if extractable_attr == PyKCS11.CK_FALSE:
                        logger.critical(
                            f"HSM key '{key_label}' is marked "
                            "CKA_EXTRACTABLE=FALSE. Refusing to "
                            "extract CKA_VALUE into process memory; "
                            "callers must move to HSM-managed "
                            "cryptographic operations."
                        )
                        return None
                    logger.info(f"Retrieved existing key '{key_label}' from HSM")
                    return bytes(value_attr)
                else:
                    if not self._hsm_allow_generate():
                        raise HsmCryptoError(
                            f"HSM key '{key_label}' not found and allow_generate=False"
                        )
                    logger.info(f"Key '{key_label}' not found in HSM, creating new key")

                    new_key = os.urandom(32)
                    extractable = self._hsm_key_extractable()

                    key_template = [
                        (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
                        (PyKCS11.CKA_KEY_TYPE, PyKCS11.CKK_AES),
                        (PyKCS11.CKA_VALUE_LEN, 32),
                        (PyKCS11.CKA_LABEL, key_label),
                        (PyKCS11.CKA_ENCRYPT, PyKCS11.CK_TRUE),
                        (PyKCS11.CKA_DECRYPT, PyKCS11.CK_TRUE),
                        (
                            PyKCS11.CKA_EXTRACTABLE,
                            PyKCS11.CK_TRUE if extractable else PyKCS11.CK_FALSE,
                        ),
                        (PyKCS11.CKA_PRIVATE, PyKCS11.CK_TRUE),
                    ]
                    # Only ship plaintext into the token when honestly marked
                    # extractable; otherwise create a token-random key.
                    key_template.append(
                        (PyKCS11.CKA_VALUE, new_key if extractable else os.urandom(32))
                    )

                    session.createObject(key_template)
                    if not extractable:
                        logger.info(
                            f"Created non-extractable key '{key_label}' in HSM; "
                            "use hsm_encrypt()/hsm_decrypt()."
                        )
                        return None
                    logger.warning(
                        f"Created new extractable-key '{key_label}' in HSM. "
                        "Consider migrating to key_extractable=False + "
                        "hsm_encrypt()/hsm_decrypt() for stronger "
                        "non-extractable guarantees."
                    )
                    return new_key

            finally:
                try:
                    session.logout()
                except Exception:
                    pass
                try:
                    session.closeSession()
                except Exception:
                    pass

        except (HsmCryptoError, ImportError):
            raise
        except Exception as e:
            logger.error(f"HSM key retrieval/creation failed: {e}")
            raise

    def _get_tpm_key(self) -> Optional[bytes]:
        """Derive a TPM-bound KEK via HKDF over the primary public + name.

        SECURITY: the previous implementation hashed a fixed label with
        ``ctx.hash()`` — a pure software hash identical on every host, so
        the "TPM key" was neither unique nor bound to any TPM. We now
        create a stable primary under the configured hierarchy, read its
        public area + object name (unique per TPM seed + template), and
        HKDF them into a 32-byte KEK.
        """
        if not self._tpm_available:
            return None

        try:
            tpm2_pytss = cast(Any, importlib.import_module("tpm2_pytss"))
            if tpm2_pytss is None:
                raise ImportError("tpm2_pytss is None")
            tpm2_constants = cast(Any, importlib.import_module("tpm2_pytss.constants"))
            ESYS_Context = tpm2_pytss.ESYS_Context
            TPM2B_PUBLIC = tpm2_pytss.TPM2B_PUBLIC
            TPM2B_SENSITIVE_CREATE = tpm2_pytss.TPM2B_SENSITIVE_CREATE
            ESYS_TR = tpm2_constants.ESYS_TR
            TPMA_OBJECT = tpm2_constants.TPMA_OBJECT

            cfg = self._tpm_config or {}
            label = str(cfg.get("label", "PLEXICHAT_KEK_V2"))
            salt = str(cfg.get("salt", "plexichat:tpm-kek:v2"))
            hierarchy_name = str(cfg.get("hierarchy", "owner")).upper()
            hierarchy = getattr(ESYS_TR, hierarchy_name, ESYS_TR.OWNER)
            template_name = str(cfg.get("key_template", "aes256"))
            persistent = cfg.get("persistent_handle", "")

            with ESYS_Context() as ctx:
                in_public = TPM2B_PUBLIC.parse(
                    alg=template_name,
                    objectAttributes=(
                        TPMA_OBJECT.USERWITHAUTH
                        | TPMA_OBJECT.RESTRICTED
                        | TPMA_OBJECT.DECRYPT
                        | TPMA_OBJECT.FIXEDTPM
                        | TPMA_OBJECT.FIXEDPARENT
                        | TPMA_OBJECT.SENSITIVEDATAORIGIN
                    ),
                )

                primary_handle, out_public, _, _, _ = ctx.create_primary(
                    hierarchy,
                    TPM2B_SENSITIVE_CREATE(),
                    in_public,
                )
                try:
                    if persistent not in (None, ""):
                        try:
                            ctx.evict_control(
                                hierarchy, primary_handle, int(str(persistent), 0)
                            )
                        except Exception:
                            pass  # persistence is best-effort
                    public_bytes = self._tpm_marshal_public(out_public)
                    name_bytes = self._tpm_object_name(ctx, primary_handle, out_public)
                finally:
                    try:
                        ctx.flush_context(primary_handle)
                    except Exception:
                        pass

                return tpm_bound_kek(public_bytes, name_bytes, label=label, salt=salt)

        except Exception as e:
            logger.debug(f"TPM key derivation failed: {e}")
            return None

    @staticmethod
    def _tpm_marshal_public(out_public: Any) -> bytes:
        for attr in ("marshal", "to_bytes", "bytes"):
            try:
                meth = getattr(out_public, attr, None)
                if callable(meth):
                    data = cast(Any, meth())
                    if data:
                        return bytes(data)
            except Exception:
                continue
        try:
            return repr(out_public).encode("utf-8")
        except Exception:
            return b"tpm-public"

    @staticmethod
    def _tpm_object_name(ctx: Any, handle: Any, out_public: Any) -> bytes:
        for expr in (
            lambda: ctx.get_name(handle),
            lambda: ctx.tr_get_name(handle),
            lambda: getattr(out_public, "get_name", lambda: None)(),
            lambda: getattr(getattr(out_public, "name", None), "buffer", None),
        ):
            try:
                name = expr()
                if name is None:
                    continue
                buf = cast(Any, getattr(name, "buffer", name))
                if buf:
                    return bytes(buf)
            except Exception:
                continue
        raise HsmCryptoError("could not read TPM object name")

    def get_hardware_status(self) -> Dict[str, Any]:
        """Introspection for health checks / admin panels (no secrets)."""
        return {
            "kek_source": self._source,
            "hsm_available": self._hsm_available,
            "hsm_backend": self._hsm_backend or None,
            "hsm_managed_crypto": self.supports_hsm_crypto(),
            "tpm_enabled": self._tpm_enabled,
            "tpm_available": self._tpm_available,
        }

    def is_using_secure_source(self) -> bool:
        """Check if the current master key is from a secure source (Env, HSM, or TPM)."""
        # Ensure key is loaded
        if not self._master_key:
            self.get_kek()
        return self._source in ("env", "hsm", "tpm")

    def get_source(self) -> str:
        """Get the current KEK source."""
        if not self._master_key:
            self.get_kek()
        return self._source


def _load_machine_key_impl(key_path: str) -> bytes:
    """Load (or generate) the machine-local encryption key from disk.

    This is the actual disk-reading implementation; it is wrapped by
    :func:`_load_machine_key_cached` so that all three keyrings (system,
    file, message) share a single load per process.
    """
    key_file = Path(key_path)
    if key_file.exists():
        try:
            data = key_file.read_bytes()
            if len(data) == 32:
                logger.debug("Loaded machine-local encryption key from file")
                logger.debug(
                    f"Machine key loaded ({len(data)} bytes) from {key_file} - "
                    "this should appear only once per process per key file"
                )
                return data
        except Exception as e:
            logger.error(f"Failed to read machine key: {e}")

    # Generate new machine-local key
    logger.warning(
        "CRITICAL SECURITY WARNING: Generating machine-local encryption key. "
        "This key is NOT hardware-bound and is less secure than HSM, TPM, or "
        "Environment Variable. For production deployments, set the appropriate "
        "PLEXICHAT_*_KEY environment variable or configure HSM/TPM."
    )
    new_key = os.urandom(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(new_key)
        # Restrict permissions
        if os.name == "nt":
            # On Windows, use icacls to restrict access to current user only.
            #
            # SECURITY: the previous implementation passed ``%USERNAME%``
            # as a literal to ``subprocess.run(..., shell=False)``.
            # ``shell=False`` does NOT expand environment variables, so
            # Windows received the literal string ``%USERNAME%`` and
            # silently created an ACL entry for a user named
            # ``%USERNAME%`` — the key file effectively became world
            # readable.
            #
            # We now resolve ``USERNAME`` via ``os.environ`` first
            # (with a ``getpass.getuser()`` fallback for hosts that
            # only expose ``LOGNAME``) and hand icacls a concrete name.
            try:
                import subprocess
                import getpass

                current_user = os.environ.get("USERNAME") or getpass.getuser()
                if not current_user:
                    current_user = ""
                subprocess.run(
                    [
                        "icacls",
                        str(key_file),
                        "/inheritance:r",
                        "/grant:r",
                        "*S-1-5-32-544:F",
                    ]
                    + (
                        [
                            "/grant:r",
                            f"{current_user}:F",
                        ]
                        if current_user
                        else []
                    ),
                    capture_output=True,
                    check=False,
                )
            except Exception:
                pass
        else:
            try:
                os.chmod(key_file, 0o600)
            except (OSError, AttributeError):
                pass
        logger.info(f"Machine-local key saved to {key_file}")
    except Exception as e:
        logger.error(f"Failed to persist machine key: {e}")

    logger.debug(
        f"Machine key loaded ({len(new_key)} bytes) from {key_file} - "
        "this should appear only once per process per key file"
    )
    return new_key


@lru_cache(maxsize=4)
def _load_machine_key_cached(key_path: str) -> bytes:
    """Cached wrapper around :func:`_load_machine_key_impl`.

    Caches up to 4 key paths (system, file, message, and a safety margin).
    Multiple :class:`HardwareVault` instances — one per keyring — share
    the resulting bytes so the key file is read from disk only once.
    """
    return _load_machine_key_impl(key_path)


# Global vault instance for backward compatibility
vault = HardwareVault()
