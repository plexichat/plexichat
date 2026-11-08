"""Typed vault models."""

from __future__ import annotations

from typing import Any, Mapping, TypedDict


class TpmConfig(TypedDict, total=False):
    enabled: bool
    label: str
    salt: str
    hierarchy: str
    key_template: str
    persistent_handle: str | int | None


class HsmConfig(TypedDict, total=False):
    enabled: bool
    library_path: str
    slot_id: int | str | None
    pin: str
    pin_env: str
    pin_file: str
    key_label: str
    token_label: str | None
    key_extractable: bool
    allow_generate: bool


class HardwareRootConfig(TypedDict, total=False):
    require_hardware: bool
    mode: str


class HardwareStatus(TypedDict):
    kek_source: str
    hsm_available: bool
    hsm_backend: str | None
    hsm_managed_crypto: bool
    tpm_enabled: bool
    tpm_available: bool


class SourcePolicy(HardwareStatus, total=False):
    secure_source: bool
    hardware_rooted: bool


def as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}
