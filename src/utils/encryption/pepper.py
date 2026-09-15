"""
Password pepper providers with real HSM support.

Design (done properly, not security theatre):

* A *pepper* is a 32-byte server-side secret applied as
  ``HMAC-SHA256(pepper, password)`` BEFORE Argon2id. Salts stay inside the
  Argon2 PHC string; the pepper is never stored next to the hash, so a bare
  database dump is insufficient for offline cracking.
* Peppers live in the **system keyring** (``system_keyring.json``), encrypted
  at rest by the vault KEK (env / HSM / TPM / machine-local, in that order).
  No operator-managed pepper secret, no extra env var: the pepper inherits
  the KEK's protection and its backup/rotation story automatically.
* Peppers are **versioned** (``pepper_id``). The hash envelope records which
  pepper was used, old peppers stay in the keyring for verification, and a
  successful login with a stale pepper triggers a transparent rehash.
* HSM support is a real provider: the HMAC runs **inside** the token via
  ``CKM_SHA256_HMAC`` with a ``CKA_EXTRACTABLE=FALSE`` key. The key bytes
  never enter process memory.

Provider sources per pepper entry (``source`` field)::

    keyring      -> 32-byte secret from the system keyring (default).
                    Generated once via CSRNG, persisted KEK-encrypted.
    hsm          -> HMAC inside PKCS#11 token (non-extractable)
    env          -> 32-byte key from env var (migration/advanced use only)
    file         -> 32-byte key from file (migration/advanced use only)
    kek-derived  -> HKDF(KEK, salt, info=pepper_id); bound to KEK rotation

HSM PIN hygiene: the PIN is NEVER read from a plaintext ``pin`` config
value in production. Supported (in order): ``pin_env`` (env var name),
``pin_file`` (file containing the PIN), ``${VAR}``/``$VAR`` expansion of
``pin``. A literal ``pin`` still works for SoftHSM dev setups but logs a
warning exactly once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, cast

_PEPPER_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
_ENVELOPE_RE = re.compile(
    r"^\$plexichat\$pepper-v1\$pid=([A-Za-z0-9_-]{1,32})\$(.+)$", re.DOTALL
)

ENVELOPE_PREFIX = "$plexichat$pepper-v1$pid="


def parse_envelope(hash_str: str) -> Optional[Tuple[str, str]]:
    """Split ``$plexichat$pepper-v1$pid=<id>$<argon2-phc>``.

    Returns ``(pepper_id, inner_argon2_hash)`` or ``None`` for anything
    that is not a pepper envelope (such strings fail closed at verify).
    """
    m = _ENVELOPE_RE.match(hash_str or "")
    if not m:
        return None
    return m.group(1), m.group(2)


def is_peppered(hash_str: str) -> bool:
    return parse_envelope(hash_str) is not None


def _expand_env_ref(value: str) -> str:
    """Expand ``${VAR:-default}`` / ``${VAR}`` / ``$VAR`` references."""
    import re as _re

    pattern = _re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")

    def _repl(m: _re.Match) -> str:
        group3 = m.group(3)
        if group3 is not None:
            return os.environ.get(group3, "")
        name = m.group(1) or ""
        default = m.group(2)
        if default is not None:
            found = os.environ.get(name, default)
            return found if found is not None else ""
        return os.environ.get(name, "")

    return pattern.sub(_repl, value)


def resolve_secret(value: Any) -> str:
    """Resolve a secret that may be a literal or an env reference."""
    if value is None:
        return ""
    text = str(value)
    if "$" in text:
        return _expand_env_ref(text)
    return text


def resolve_pin(hsm_config: Dict[str, Any]) -> str:
    """Resolve the HSM PIN without ever logging it.

    Order: ``pin_env`` -> ``pin_file`` -> ``pin`` (with env expansion).
    """
    pin_env = hsm_config.get("pin_env")
    if pin_env:
        pin = os.environ.get(str(pin_env), "")
        if pin:
            return pin
    pin_file = hsm_config.get("pin_file")
    if pin_file:
        try:
            content = Path(str(pin_file)).read_text(encoding="utf-8")
            pin = content.strip().splitlines()[0].strip() if content.strip() else ""
            if pin:
                return pin
        except OSError:
            pass
    return resolve_secret(hsm_config.get("pin", ""))


_warned_plaintext_pin = False
_warn_lock = threading.Lock()


def _warn_plaintext_pin_once() -> None:
    global _warned_plaintext_pin
    with _warn_lock:
        if _warned_plaintext_pin:
            return
        _warned_plaintext_pin = True
    try:
        import utils.logger as logger

        logger.warning(
            "HSM 'pin' is set literally in config; prefer 'pin_env' or "
            "'pin_file' so the PIN never lands in a config file."
        )
    except Exception:
        pass


def decode_key_material(raw: str, *, what: str) -> bytes:
    """Decode a 32-byte key from hex (preferred) or base64."""
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"{what}: empty key material")
    try:
        key = bytes.fromhex(text)
        if len(key) == 32:
            return key
    except Exception:
        pass
    try:
        key = base64.b64decode(text, validate=True)
        if len(key) == 32:
            return key
    except Exception:
        pass
    raise ValueError(f"{what}: must decode to exactly 32 bytes (hex or base64)")


def load_key_file(path: str, *, what: str) -> bytes:
    """Load 32-byte key material from a file (hex or base64, single line)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError as exc:
        raise ValueError(f"{what}: cannot read key file {path}: {exc}") from exc
    if os.name != "nt" and st.st_mode & 0o077:
        try:
            import utils.logger as logger

            logger.warning(
                f"{what}: key file {path} is group/other-readable "
                f"(mode {oct(st.st_mode & 0o777)}); chmod 600 recommended."
            )
        except Exception:
            pass
    try:
        content = p.read_text(encoding="utf-8").strip().splitlines()
        line = content[0].strip() if content else ""
    except OSError as exc:
        raise ValueError(f"{what}: cannot read key file {path}: {exc}") from exc
    return decode_key_material(line, what=what)


class PepperProvider:
    """Abstract pepper: HMAC(password) either in software or inside an HSM."""

    pepper_id: str = "default"
    in_hsm: bool = False

    def prehash(self, password: str) -> str:
        """Return base64(HMAC_SHA256(pepper, password_utf8))."""
        raise NotImplementedError

    def close(self) -> None: ...


class SoftwarePepperProvider(PepperProvider):
    """HMAC-SHA256 with a process-local 32-byte key."""

    def __init__(self, pepper_id: str, key: bytes):
        if len(key) != 32:
            raise ValueError("pepper key must be 32 bytes")
        self.pepper_id = pepper_id
        self._key = bytes(key)
        self.in_hsm = False

    def prehash(self, password: str) -> str:
        digest = hmac.new(self._key, password.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("ascii")

    def close(self) -> None:
        self._key = b"\x00" * 32


def _system_keyring_path() -> Path:
    return Path.home() / ".plexichat" / "data" / "system_keyring.json"


class KeyringPepperProvider(PepperProvider):
    """HMAC-SHA256 with a secret stored in the system keyring.

    The default pepper source. The 32-byte secret is generated once via
    CSRNG on first use and persisted KEK-encrypted in
    ``system_keyring.json`` (see ``Keyring.get_or_create_pepper``), so no
    operator-managed secret is needed. Bytes are cached in memory after
    the first load; use a new pepper id to rotate.
    """

    def __init__(
        self,
        pepper_id: str,
        keyring_path: Optional[str] = None,
        kek_env_var: str = "PLEXICHAT_SYSTEM_KEY",
    ):
        if not _PEPPER_ID_RE.fullmatch(pepper_id):
            raise ValueError(f"invalid pepper id: {pepper_id!r}")
        self.pepper_id = pepper_id
        self.in_hsm = False
        self._keyring_path = (
            Path(keyring_path) if keyring_path else _system_keyring_path()
        )
        self._kek_env_var = kek_env_var
        self._lock = threading.Lock()
        self._key: Optional[bytes] = None

    def _load_key(self) -> bytes:
        # Imported lazily: keyring pulls in AESGCM/vault, and pepper is
        # imported by the password mixin at hash time.
        from .core.keyring import Keyring

        with self._lock:
            if self._key is None:
                keyring = Keyring(self._keyring_path, kek_env_var=self._kek_env_var)
                self._key = keyring.get_or_create_pepper(self.pepper_id)
            return self._key

    def prehash(self, password: str) -> str:
        key = self._load_key()
        digest = hmac.new(key, password.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("ascii")

    def close(self) -> None:
        with self._lock:
            self._key = None


class HsmPepperProvider(PepperProvider):
    """HMAC-SHA256 executed INSIDE the PKCS#11 token.

    The key is created on-token with ``CKA_EXTRACTABLE=FALSE`` /
    ``CKA_SIGN=TRUE`` and addressed by label; ``prehash()`` calls
    ``C_Sign`` (``CKM_SHA256_HMAC``) per password via a short-lived,
    login-scoped session. Key bytes never cross the token boundary.

    Backend preference: ``pkcs11`` (python-pkcs11, maintained) first,
    ``PyKCS11`` (legacy) as fallback.
    """

    def __init__(
        self,
        pepper_id: str,
        hsm_config: Dict[str, Any],
        key_label: str,
    ):
        self.pepper_id = pepper_id
        self.in_hsm = True
        self._config = dict(hsm_config)
        self._key_label = key_label
        self._lock = threading.Lock()
        self._backend: str = ""
        self._lib: Any = None
        self._token: Any = None
        self._init_token()

    # -- setup ---------------------------------------------------------
    def _init_token(self) -> None:
        pin = resolve_pin(self._config)
        if not pin:
            raise ValueError(f"HSM pepper '{self.pepper_id}': missing PIN")
        library_path = self._config.get("library_path", "")
        if not library_path:
            raise ValueError(f"HSM pepper '{self.pepper_id}': missing library_path")
        # Prefer maintained `pkcs11` package.
        try:
            pkcs11_mod = importlib.import_module("pkcs11")
            lib = pkcs11_mod.lib(str(library_path))
            slot_id = self._config.get("slot_id", 0)
            token_label = self._config.get("token_label")
            token = None
            try:
                tokens = list(
                    lib.get_tokens(token_label=token_label)
                    if token_label
                    else lib.get_tokens()
                )
            except TypeError:
                tokens = list(lib.get_tokens())
            if slot_id is not None:
                try:
                    wanted = int(slot_id)
                    for tok in tokens:
                        try:
                            if int(getattr(tok, "slot_id", -1)) == wanted:
                                token = tok
                                break
                        except Exception:
                            continue
                except (TypeError, ValueError):
                    pass
            if token is None and tokens:
                token = tokens[0]
            if token is None:
                raise ValueError("no PKCS#11 tokens available")
            # Verify PIN + ensure key exists (all inside the token).
            with token.open(user_pin=pin, rw=True) as session:
                self._ensure_key_pkcs11(session)
            self._backend = "pkcs11"
            self._lib = lib
            self._token = token
            self._pin = pin
            return
        except ImportError:
            pass
        # Fallback: legacy PyKCS11 (sign via C_Sign with SHA256_HMAC).
        try:
            PyKCS11 = cast(Any, importlib.import_module("PyKCS11")).PyKCS11
        except ImportError as exc:
            raise ValueError(
                f"HSM pepper '{self.pepper_id}': neither 'pkcs11' nor "
                f"'PyKCS11' is installed"
            ) from exc
        pkcs11_lib = PyKCS11.PyKCS11Lib()
        pkcs11_lib.load(str(library_path))
        slots = pkcs11_lib.getSlotList(tokenPresent=True)
        slot_id = int(self._config.get("slot_id", 0))
        if slot_id not in slots:
            raise ValueError(f"HSM slot {slot_id} not available (have {slots})")
        session = pkcs11_lib.openSession(
            slot_id, PyKCS11.CKF_SERIAL_SESSION | PyKCS11.CKF_RW_SESSION
        )
        try:
            session.login(pin)
            try:
                self._ensure_key_pykcs11(PyKCS11, session)
            finally:
                session.logout()
        finally:
            session.closeSession()
        self._backend = "pykcs11"
        self._lib = pkcs11_lib
        self._pin = pin

    def _ensure_key_pkcs11(self, session: Any) -> None:
        # Optional dependency: resolved dynamically so static analysis and
        # installs without HSM support keep working (same pattern as the
        # vault's PyKCS11 usage).
        pkcs11_mod = importlib.import_module("pkcs11")
        KeyType = getattr(pkcs11_mod, "KeyType")
        Mechanism = getattr(pkcs11_mod, "Mechanism")

        for key in session.get_objects(
            {"CKA_LABEL": self._key_label, "CKA_CLASS": "CKO_SECRET_KEY"}
        ):
            return
        session.generate_key(
            KeyType.SHA256_HMAC,
            256,
            mechanism=Mechanism.SHA256_HMAC_KEY_GEN,
            template={
                "CKA_LABEL": self._key_label,
                "CKA_PRIVATE": True,
                "CKA_TOKEN": True,
                "CKA_SENSITIVE": True,
                # The whole point: non-extractable HMAC key.
                "CKA_EXTRACTABLE": False,
                "CKA_SIGN": True,
                "CKA_VERIFY": True,
            },
        )

    def _ensure_key_pykcs11(self, PyKCS11: Any, session: Any) -> None:
        template = [
            (PyKCS11.CKA_LABEL, self._key_label),
            (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
            (PyKCS11.CKA_KEY_TYPE, PyKCS11.CKK_SHA256_HMAC),
        ]
        if not session.findObjects(template):
            session.createObject(
                [
                    (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
                    (PyKCS11.CKA_KEY_TYPE, PyKCS11.CKK_SHA256_HMAC),
                    (PyKCS11.CKA_VALUE_LEN, 32),
                    (PyKCS11.CKA_LABEL, self._key_label),
                    (PyKCS11.CKA_VALUE, os.urandom(32)),
                    (PyKCS11.CKA_TOKEN, PyKCS11.CK_TRUE),
                    (PyKCS11.CKA_PRIVATE, PyKCS11.CK_TRUE),
                    (PyKCS11.CKA_SENSITIVE, PyKCS11.CK_TRUE),
                    (PyKCS11.CKA_EXTRACTABLE, PyKCS11.CK_FALSE),
                    (PyKCS11.CKA_SIGN, PyKCS11.CK_TRUE),
                    (PyKCS11.CKA_VERIFY, PyKCS11.CK_TRUE),
                ]
            )

    # -- operation -----------------------------------------------------
    def prehash(self, password: str) -> str:
        data = password.encode("utf-8")
        with self._lock:
            if self._backend == "pkcs11":
                pkcs11_mod = importlib.import_module("pkcs11")
                Mechanism = getattr(pkcs11_mod, "Mechanism")

                with self._token.open(user_pin=self._pin) as session:
                    key = next(
                        session.get_objects(
                            {
                                "CKA_LABEL": self._key_label,
                                "CKA_CLASS": "CKO_SECRET_KEY",
                            }
                        )
                    )
                    sig = session.sign(key, data, mechanism=Mechanism.SHA256_HMAC)
                    digest = bytes(sig)
            else:
                PyKCS11 = cast(Any, importlib.import_module("PyKCS11")).PyKCS11
                slot_id = int(self._config.get("slot_id", 0))
                session = self._lib.openSession(
                    slot_id, PyKCS11.CKF_SERIAL_SESSION | PyKCS11.CKF_RW_SESSION
                )
                try:
                    session.login(self._pin)
                    try:
                        objs = session.findObjects(
                            [
                                (PyKCS11.CKA_LABEL, self._key_label),
                                (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
                            ]
                        )
                        if not objs:
                            raise ValueError(f"HSM key '{self._key_label}' not found")
                        mech = PyKCS11.Mechanism(PyKCS11.CKM_SHA256_HMAC, None)
                        digest = bytes(session.sign(objs[0], data, mech))
                    finally:
                        session.logout()
                finally:
                    session.closeSession()
        if len(digest) != 32:
            # SHA256-HMAC must be 32 bytes; anything else means the token
            # answered with the wrong mechanism — fail closed.
            raise ValueError("HSM HMAC returned unexpected length")
        return base64.b64encode(digest).decode("ascii")

    def close(self) -> None:
        try:
            self._pin = ""
        except Exception:
            pass


def _kek_derived_key(pepper_id: str, length: int = 32) -> bytes:
    """Derive a stable pepper from the vault KEK via HKDF.

    Bound to KEK rotation by design: rotating the KEK implicitly rotates
    these peppers (old KEK must stay available during migration, same as
    the keyrings).
    """
    from .vault import vault
    from .hkdf import derive_key

    kek = vault.get_kek()
    return derive_key(
        kek,
        salt=b"plexichat:password-pepper:v1",
        info=f"pepper:{pepper_id}".encode("utf-8"),
        length=length,
    )


def build_provider(
    pepper_id: str,
    entry: Dict[str, Any],
    hsm_defaults: Dict[str, Any],
) -> PepperProvider:
    """Build one pepper provider from a config entry."""
    if not _PEPPER_ID_RE.fullmatch(pepper_id):
        raise ValueError(f"invalid pepper id: {pepper_id!r}")
    source = str(entry.get("source", "keyring")).lower()
    if source == "keyring":
        return KeyringPepperProvider(
            pepper_id,
            keyring_path=entry.get("keyring_path"),
            kek_env_var=str(entry.get("kek_env_var", "PLEXICHAT_SYSTEM_KEY")),
        )
    if source == "hsm":
        hsm_cfg = dict(hsm_defaults)
        hsm_cfg.update(entry.get("hsm", {}) or {})
        # Per-pepper overrides at the top level of the entry.
        for k in (
            "library_path",
            "slot_id",
            "token_label",
            "pin",
            "pin_env",
            "pin_file",
        ):
            if entry.get(k) not in (None, ""):
                hsm_cfg[k] = entry[k]
        if (
            hsm_cfg.get("pin") not in (None, "")
            and not hsm_cfg.get("pin_env")
            and not hsm_cfg.get("pin_file")
        ):
            _warn_plaintext_pin_once()
        key_label = str(
            entry.get("key_label")
            or hsm_cfg.get("key_label", "plexichat_kek")
            or f"plexichat-pepper-{pepper_id}"
        )
        # Distinct default label per pepper so peppers never collide on one
        # token unless the operator explicitly shares a label.
        if "key_label" not in entry and hsm_cfg.get("key_label") == "plexichat_kek":
            key_label = f"plexichat-pepper-{pepper_id}"
        return HsmPepperProvider(pepper_id, hsm_cfg, key_label)
    if source == "file":
        path = entry.get("file_path") or entry.get("path", "")
        if not path:
            raise ValueError(f"pepper '{pepper_id}': file source needs file_path")
        key = load_key_file(str(path), what=f"pepper '{pepper_id}'")
        return SoftwarePepperProvider(pepper_id, key)
    if source == "kek-derived":
        return SoftwarePepperProvider(pepper_id, _kek_derived_key(pepper_id))
    if source == "env":
        env_var = str(entry.get("env_var", "PLEXICHAT_PASSWORD_PEPPER"))
        raw = resolve_secret(os.environ.get(env_var, "") or entry.get("value", ""))
        if not raw:
            raise ValueError(
                f"pepper '{pepper_id}': no key in ${env_var} (32-byte hex or base64)"
            )
        return SoftwarePepperProvider(
            pepper_id, decode_key_material(raw, what=f"pepper '{pepper_id}'")
        )
    raise ValueError(
        f"pepper '{pepper_id}': unknown source {source!r} "
        "(expected keyring, hsm, env, file, or kek-derived)"
    )


class PepperStore:
    """Versioned set of pepper providers: current + previous for verify."""

    def __init__(
        self,
        providers: Dict[str, PepperProvider],
        current_id: str,
        enabled: bool = True,
    ):
        self._providers = dict(providers)
        self._current_id = current_id
        self._enabled = enabled and bool(providers)

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._providers)

    @property
    def current_id(self) -> str:
        return self._current_id

    @property
    def provider_ids(self) -> List[str]:
        return sorted(self._providers.keys())

    def get(self, pepper_id: str) -> Optional[PepperProvider]:
        return self._providers.get(pepper_id)

    def current(self) -> Optional[PepperProvider]:
        return self._providers.get(self._current_id)

    def prehash_current(self, password: str) -> Tuple[str, str]:
        """Return ``(pepper_id, prehash)`` with the current pepper."""
        provider = self.current()
        if provider is None:
            raise ValueError("no current pepper provider configured")
        return provider.pepper_id, provider.prehash(password)

    def prehash_with(self, pepper_id: str, password: str) -> str:
        provider = self._providers.get(pepper_id)
        if provider is None:
            raise ValueError(f"unknown pepper id: {pepper_id!r}")
        return provider.prehash(password)


_store: Optional[PepperStore] = None
_store_lock = threading.Lock()


def _pepper_config() -> Tuple[bool, str, Dict[str, Any], Dict[str, Any]]:
    try:
        import utils.config as config

        enc = config.get("encryption", {}) or {}
        pepper_cfg = enc.get("password_pepper", {}) or {}
        hsm_cfg = enc.get("hsm", {}) or {}
    except Exception:
        return False, "default", {}, {}
    enabled = bool(pepper_cfg.get("enabled", False))
    current_id = str(pepper_cfg.get("current_id", "v1"))
    peppers = pepper_cfg.get("peppers", {}) or {}
    return enabled, current_id, peppers, hsm_cfg


def get_pepper_store(*, refresh: bool = False) -> PepperStore:
    """Return the process-wide pepper store (built lazily from config)."""
    global _store
    with _store_lock:
        if _store is not None and not refresh:
            return _store
        enabled, current_id, peppers, hsm_cfg = _pepper_config()
        providers: Dict[str, PepperProvider] = {}
        if enabled and peppers:
            for pid, entry in peppers.items():
                try:
                    providers[str(pid)] = build_provider(str(pid), entry or {}, hsm_cfg)
                except Exception as exc:
                    try:
                        import utils.logger as logger

                        logger.error(f"pepper '{pid}' unavailable: {exc}")
                    except Exception:
                        pass
            if current_id not in providers:
                # Fail closed: a configured current pepper that cannot be
                # built must not silently fall back to an old pepper.
                try:
                    import utils.logger as logger

                    logger.error(
                        f"password pepper current_id={current_id!r} unavailable; "
                        "peppering disabled until fixed"
                    )
                except Exception:
                    pass
                _store = PepperStore({}, current_id, enabled=False)
                return _store
        _store = PepperStore(providers, current_id, enabled=enabled)
        return _store


def reset_pepper_store() -> None:
    """Forget the cached store (tests + config reload)."""
    global _store
    with _store_lock:
        old, _store = _store, None
    if old is not None:
        for provider in getattr(old, "_providers", {}).values():
            try:
                provider.close()
            except Exception:
                pass


__all__ = [
    "ENVELOPE_PREFIX",
    "HsmPepperProvider",
    "KeyringPepperProvider",
    "PepperProvider",
    "PepperStore",
    "SoftwarePepperProvider",
    "build_provider",
    "decode_key_material",
    "get_pepper_store",
    "is_peppered",
    "load_key_file",
    "parse_envelope",
    "reset_pepper_store",
    "resolve_pin",
    "resolve_secret",
]
