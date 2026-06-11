"""
Serialization and cache key generation utilities.

Provides _ensure_serializable, _reconstruct_object, and _generate_cache_key
for the cache module.
"""

import json
import hashlib
import dataclasses
from enum import Enum
from typing import Any, Dict, Optional

import utils.logger as logger

# Type registry for reconstruction
_TYPE_REGISTRY: Dict[str, type] = {}


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
        data = ensure_serializable(dataclasses.asdict(obj))
        if isinstance(data, dict):
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


def get_type_from_name(type_name: str) -> Optional[type]:
    """Dynamically load and cache types for reconstruction."""
    global _TYPE_REGISTRY
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
                    return cls(reconstructed_params)
                return cls(**reconstructed_params)
            except Exception as e:
                logger.debug(f"Failed to reconstruct {type_name}: {e}")
                return reconstructed_params
        else:
            return {k: reconstruct_object(v) for k, v in data.items()}

    return data
