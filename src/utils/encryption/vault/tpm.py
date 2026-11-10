"""TPM 2.0 KEK support."""

from __future__ import annotations

import importlib
from typing import Any, Mapping, cast

from .config import hardware_required, load_tpm_config
from .constants import (
    TPM_DEFAULT_HIERARCHY,
    TPM_DEFAULT_LABEL,
    TPM_DEFAULT_SALT,
    TPM_DEFAULT_TEMPLATE,
)
from .errors import HsmCryptoError
from .tpm_kdf import tpm_bound_kek


def _log_debug(message: str, *args: Any) -> None:
    try:
        import utils.logger as logger

        logger.debug(message, *args)
    except RuntimeError:
        pass


def _log_info(message: str, *args: Any) -> None:
    try:
        import utils.logger as logger

        logger.info(message, *args)
    except RuntimeError:
        pass


def _log_error(message: str, *args: Any) -> None:
    try:
        import utils.logger as logger

        logger.error(message, *args)
    except RuntimeError:
        pass


def init_tpm(
    tpm_config: dict[str, Any] | None = None,
    *,
    require_hw: bool | None = None,
) -> tuple[bool, bool, dict[str, Any]]:
    """Initialize TPM availability.

    Returns (tpm_available, tpm_enabled, tpm_config).
    """
    cfg = dict(tpm_config) if tpm_config is not None else dict(load_tpm_config())
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        _log_debug("TPM 2.0 disabled in configuration")
        return False, False, cfg

    hw_required = hardware_required() if require_hw is None else require_hw
    try:
        tpm2_pytss = cast(Any, importlib.import_module("tpm2_pytss"))
        if tpm2_pytss is None:
            raise ImportError("tpm2_pytss is None")
        esys_context = tpm2_pytss.ESYS_Context

        with esys_context() as ctx:
            cap = ctx.get_capability(0, 0x00000001, 1)  # TPM_CAP_PROPERTIES
            if cap:
                _log_info("TPM 2.0 hardware detected and available")
                return True, True, cfg
    except (ImportError, Exception) as exc:
        if hw_required:
            _log_error(
                "TPM 2.0 required by hardware_root_of_trust policy but unavailable: %s",
                exc,
            )
        else:
            _log_debug("TPM 2.0 not available or tpm2-pytss not installed")
    return False, True, cfg


def get_tpm_key(tpm_config: Mapping[str, Any]) -> bytes | None:
    """Derive a TPM-bound KEK via HKDF over the primary public + name."""
    try:
        tpm2_pytss = cast(Any, importlib.import_module("tpm2_pytss"))
        if tpm2_pytss is None:
            raise ImportError("tpm2_pytss is None")
        tpm2_constants = cast(Any, importlib.import_module("tpm2_pytss.constants"))
        esys_context = tpm2_pytss.ESYS_Context
        tpm2b_public = tpm2_pytss.TPM2B_PUBLIC
        tpm2b_sensitive_create = tpm2_pytss.TPM2B_SENSITIVE_CREATE
        esys_tr = tpm2_constants.ESYS_TR
        tpma_object = tpm2_constants.TPMA_OBJECT

        label = str(tpm_config.get("label", TPM_DEFAULT_LABEL))
        salt = str(tpm_config.get("salt", TPM_DEFAULT_SALT))
        hierarchy_name = str(tpm_config.get("hierarchy", TPM_DEFAULT_HIERARCHY)).upper()
        hierarchy = getattr(esys_tr, hierarchy_name, esys_tr.OWNER)
        template_name = str(tpm_config.get("key_template", TPM_DEFAULT_TEMPLATE))
        persistent = tpm_config.get("persistent_handle", "")

        with esys_context() as ctx:
            in_public = tpm2b_public.parse(
                alg=template_name,
                objectAttributes=(
                    tpma_object.USERWITHAUTH
                    | tpma_object.RESTRICTED
                    | tpma_object.DECRYPT
                    | tpma_object.FIXEDTPM
                    | tpma_object.FIXEDPARENT
                    | tpma_object.SENSITIVEDATAORIGIN
                ),
            )

            primary_handle, out_public, _, _, _ = ctx.create_primary(
                hierarchy,
                tpm2b_sensitive_create(),
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
                public_bytes = marshal_tpm_public(out_public)
                name_bytes = tpm_object_name(ctx, primary_handle, out_public)
            finally:
                try:
                    ctx.flush_context(primary_handle)
                except Exception:
                    pass

            return tpm_bound_kek(public_bytes, name_bytes, label=label, salt=salt)

    except Exception as exc:
        _log_debug("TPM key derivation failed: %s", exc)
        return None


def marshal_tpm_public(out_public: Any) -> bytes:
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
        _log_debug(
            "TPM public marshaling fell back to repr(); key binding may be unstable"
        )
        return repr(out_public).encode("utf-8")
    except Exception:
        return b"tpm-public"


def tpm_object_name(ctx: Any, handle: Any, out_public: Any) -> bytes:
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
