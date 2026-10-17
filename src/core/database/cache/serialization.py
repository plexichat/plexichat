"""
Serialization and cache key generation utilities.

Provides _ensure_serializable, _reconstruct_object, and _generate_cache_key
for the cache module.
"""

import json
import hashlib
import dataclasses
import typing
from enum import Enum
from typing import Any, Dict, Optional

import utils.logger as logger

# Type registry for reconstruction
_TYPE_REGISTRY: Dict[str, type] = {}

# Allowlist of fully-qualified names that MAY be reconstructed from cached
# JSON via the ``__type__`` tag. Cached data lives in Valkey — potentially a
# shared, cluster-level instance — so anything that can write there must not
# gain arbitrary-class instantiation (SSRF/RCE gadget) in this process.
# Only classes explicitly registered through ``register_reconstructable_type``
# are eligible; everything else reconstructs as a plain dict.
_ALLOWED_TYPES: set = set()


def register_reconstructable_type(*classes: type) -> None:
    """Allowlist ``classes`` for ``__type__`` reconstruction.

    Registers each class under its ``module.QualName`` key and primes the
    lookup registry so no dynamic import is ever needed at read time.
    """
    for cls in classes:
        type_name = f"{cls.__module__}.{cls.__qualname__}"
        _ALLOWED_TYPES.add(type_name)
        _TYPE_REGISTRY[type_name] = cls


def is_reconstructable(type_name: str) -> bool:
    """Return True if ``type_name`` is on the reconstruction allowlist."""
    return type_name in _ALLOWED_TYPES


def get_type_from_name(type_name: str) -> Optional[type]:
    """Resolve an allowlisted type name to its class (see above)."""
    global _TYPE_REGISTRY
    if not is_reconstructable(type_name):
        return None
    if type_name in _TYPE_REGISTRY:
        return _TYPE_REGISTRY[type_name]

    try:
        module_path, class_name = type_name.rsplit(".", 1)
        import importlib

        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        _TYPE_REGISTRY[type_name] = cls
        return cls
    except Exception:
        return None


def generate_cache_key(prefix: str, *args, **kwargs) -> str:
    """Generate a cache key from function arguments."""
    key_parts = [prefix]

    def process_val(val: Any) -> str:
        if hasattr(val, "__class__"):
            class_name = val.__class__.__name__
            if class_name in ("TokenInfo", "User"):
                uid = getattr(val, "user_id", None) or getattr(val, "id", "unknown")
                return f"{class_name}:{uid}"
            from src.core.base import BaseManager

            try:
                if isinstance(val, BaseManager) or class_name.endswith(
                    ("Repository", "Service", "Manager")
                ):
                    return f"Core:{class_name}"
            except Exception:
                pass

        if isinstance(val, (dict, list)):
            try:
                payload = json.dumps(val, sort_keys=True, default=str)
            except Exception:
                payload = repr(val)
            digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
            return f"{type(val).__name__}:{digest}"
        return f"{type(val).__name__}:{val}"

    for arg in args:
        key_parts.append(process_val(arg))
    for k, v in sorted(kwargs.items()):
        key_parts.append(f"{k}:{process_val(v)}")

    return ":".join(key_parts)


def ensure_serializable(obj: Any) -> Any:
    """Ensure an object is JSON serializable, recursively converting complex types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj

    if isinstance(obj, Enum):
        return obj.value

    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        # Recurse field-by-field instead of ``asdict`` so nested dataclass
        # instances are serialized (and tagged) as objects rather than being
        # flattened into plain dicts and losing their type on reconstruction.
        data = {}
        for field in dataclasses.fields(obj):
            if isinstance(field.type, dataclasses.InitVar):
                # InitVar pseudo-fields are not stored as instance
                # attributes and are not part of the serialized state.
                continue
            data[field.name] = ensure_serializable(getattr(obj, field.name))
        data["__type__"] = f"{obj.__class__.__module__}.{obj.__class__.__name__}"
        return data

    if isinstance(obj, (list, tuple, set)):
        return [ensure_serializable(item) for item in obj]

    if isinstance(obj, dict):
        return {str(k): ensure_serializable(v) for k, v in obj.items()}

    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        data = ensure_serializable(model_dump())
        if isinstance(data, dict):
            obj_type = type(obj)
            data["__type__"] = f"{obj_type.__module__}.{obj_type.__name__}"
        return data
    dict_method = getattr(obj, "dict", None)
    if callable(dict_method):
        data = ensure_serializable(dict_method())
        if isinstance(data, dict):
            obj_type = type(obj)
            data["__type__"] = f"{obj_type.__module__}.{obj_type.__name__}"
        return data

    if hasattr(obj, "__str__") and not isinstance(obj, (dict, list, tuple)):
        return str(obj)

    return obj


def _coerce_dataclass_enum_fields(cls: type, params: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce plain values back into Enum members for dataclass fields.

    Enums serialize to their raw value (a JSON-friendly string), so a cached
    dataclass such as Channel (``channel_type: ChannelType``) reconstructs
    with a bare string unless the declared field type is honoured here.
    Handles direct and Optional[...] Enum fields.
    """
    if not dataclasses.is_dataclass(cls):
        return params
    try:
        hints = typing.get_type_hints(cls)
    except Exception:
        return params

    for field in dataclasses.fields(cls):
        if field.name not in params:
            continue
        value = params[field.name]
        ftype = hints.get(field.name)
        # Unwrap Optional[Enum]/Union[..., None]
        enum_cls = None
        for candidate in typing.get_args(ftype) or (ftype,):
            if isinstance(candidate, type) and issubclass(candidate, Enum):
                enum_cls = candidate
                break
        if enum_cls is not None and not isinstance(value, enum_cls):
            try:
                params[field.name] = enum_cls(value)
            except Exception:
                logger.debug(
                    f"Could not coerce field {cls.__name__}.{field.name} "
                    f"to {enum_cls.__name__} from {value!r}"
                )
    return params


def reconstruct_object(data: Any) -> Any:
    """Recursively reconstruct objects from dictionaries using __type__ hints."""
    if isinstance(data, list):
        return [reconstruct_object(item) for item in data]

    if isinstance(data, dict):
        if "__type__" in data:
            data_copy = dict(data)
            type_name = data_copy.pop("__type__")
            cls = get_type_from_name(type_name)

            if not cls:
                return data_copy

            reconstructed_params = {
                k: reconstruct_object(v) for k, v in data_copy.items()
            }

            try:
                if issubclass(cls, Enum):
                    # Enums reconstruct from a single value, not kwargs.
                    if len(reconstructed_params) == 1:
                        return cls(next(iter(reconstructed_params.values())))
                    return cls(*reconstructed_params.values())
                if dataclasses.is_dataclass(cls):
                    reconstructed_params = _coerce_dataclass_enum_fields(
                        cls, reconstructed_params
                    )
                return cls(**reconstructed_params)
            except Exception as e:
                logger.debug(f"Failed to reconstruct {type_name}: {e}")
                return reconstructed_params
        else:
            return {k: reconstruct_object(v) for k, v in data.items()}

    return data
