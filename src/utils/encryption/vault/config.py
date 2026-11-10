"""Vault configuration loading and validation."""

from __future__ import annotations

from typing import Any, Mapping

from .constants import (
    HARDWARE_REQUIRED_MODES,
    TPM_DEFAULT_HIERARCHY,
    TPM_DEFAULT_LABEL,
    TPM_DEFAULT_SALT,
    TPM_DEFAULT_TEMPLATE,
)
from .errors import VaultPolicyError
from .models import HardwareRootConfig, HsmConfig, TpmConfig, as_mapping


def _safe_config_get(*path: str) -> Any:
    try:
        import utils.config as config

        value: Any = config.get(path[0], {})
        for key in path[1:]:
            if not isinstance(value, Mapping):
                return {}
            value = value.get(key, {})
        return value
    except Exception:
        return {}


def load_tpm_config() -> TpmConfig:
    raw = as_mapping(_safe_config_get("encryption", "tpm"))
    enabled_raw = raw.get("enabled", True)
    enabled = enabled_raw if isinstance(enabled_raw, bool) else bool(enabled_raw)
    cfg: TpmConfig = {
        "enabled": enabled,
        "label": _str(raw.get("label"), TPM_DEFAULT_LABEL, max_len=128),
        "salt": _str(raw.get("salt"), TPM_DEFAULT_SALT, max_len=256),
        "hierarchy": _str(
            raw.get("hierarchy"), TPM_DEFAULT_HIERARCHY, max_len=32
        ).upper(),
        "key_template": _str(raw.get("key_template"), TPM_DEFAULT_TEMPLATE, max_len=64),
        "persistent_handle": raw.get("persistent_handle", ""),
    }
    return cfg


def load_hsm_config() -> HsmConfig:
    raw = as_mapping(_safe_config_get("encryption", "hsm"))
    cfg: HsmConfig = {
        "enabled": bool(raw.get("enabled", False)),
        "library_path": _str(raw.get("library_path"), "", max_len=4096),
        "slot_id": _slot_id(raw.get("slot_id", 0)),
        "key_label": _str(raw.get("key_label"), "plexichat_kek", max_len=128),
        "token_label": _opt_str(raw.get("token_label"), max_len=128),
        "key_extractable": bool(raw.get("key_extractable", True)),
        "allow_generate": bool(raw.get("allow_generate", True)),
    }
    for key in ("pin", "pin_env", "pin_file"):
        if key in raw and raw[key] is not None:
            value = raw[key]
            cfg[key] = str(value) if key == "pin_file" else str(value)
    return cfg


def load_hardware_root_config() -> HardwareRootConfig:
    raw = as_mapping(
        _safe_config_get("authentication", "encryption", "hardware_root_of_trust")
    )
    mode = _str(raw.get("mode"), "", max_len=32).lower()
    return {
        "require_hardware": bool(raw.get("require_hardware", False)),
        "mode": mode,
    }


def hardware_required() -> bool:
    cfg = load_hardware_root_config()
    if bool(cfg.get("require_hardware", False)):
        return True
    mode = str(cfg.get("mode", "")).lower()
    return mode in HARDWARE_REQUIRED_MODES


def require_hardware_source(source: str) -> str:
    if not hardware_required():
        return source
    if source in ("hsm", "tpm"):
        return source
    raise VaultPolicyError(
        "hardware_root_of_trust policy requires a hardware-rooted KEK (HSM/TPM), "
        f"but the resolved KEK source is {source!r}"
    )


def resolve_hsm_pin(hsm_config: Mapping[str, Any]) -> str:
    try:
        from ..pepper import resolve_pin

        pin = resolve_pin(dict(hsm_config))
    except Exception:
        pin = str(hsm_config.get("pin", ""))
    return pin if isinstance(pin, str) else str(pin or "")


def _str(value: Any, default: str, *, max_len: int) -> str:
    if value is None:
        return default
    text = str(value)
    if len(text) > max_len:
        return text[:max_len]
    return text


def _opt_str(value: Any, *, max_len: int) -> str | None:
    if value is None or value == "":
        return None
    text = str(value)
    return text[:max_len] if len(text) > max_len else text


def _slot_id(value: Any) -> int | str | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text, 0)
    except (TypeError, ValueError):
        return text
