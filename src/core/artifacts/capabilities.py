"""
Artifacts capability service.

Computes per-feature availability state for the Plexichat "Artifacts" feature
set and exposes it to the API layer. This powers admin-panel banners that tell
admins when a feature is unavailable (disabled by config, missing license,
missing dependency, or misconfigured).

The module is intentionally dependency-light and never raises: callers that
cannot reach config or licensing get a best-effort `MISCONFIGURED`/`DISABLED`
state rather than an exception.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class CapabilityState(str, Enum):
    """Availability state for a single capability/feature."""

    AVAILABLE = "available"
    DISABLED_BY_CONFIG = "disabled_by_config"
    DISABLED_BY_LICENSE = "disabled_by_license"
    DEPENDENCY_MISSING = "dependency_missing"
    MISCONFIGURED = "misconfigured"


@dataclass
class CapabilityInfo:
    """Human- and machine-readable availability description for a feature."""

    feature: str
    state: CapabilityState
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


def _load_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the artifacts config dict, loading it if not supplied."""
    if config is not None:
        return config
    try:
        import utils.config as utils_config

        artifacts = utils_config.get("artifacts", {})
        if isinstance(artifacts, dict):
            return artifacts
        return {}
    except Exception:
        return {}


def _has_feature(feature: str) -> bool:
    """Safe license feature check; returns False on any failure."""
    try:
        from utils.licensing import has_feature

        return bool(has_feature(feature, default=False))
    except Exception:
        return False


def _eval_artifacts(artifacts: Dict[str, Any]) -> CapabilityInfo:
    enabled = artifacts.get("enabled", True)
    if enabled is False:
        return CapabilityInfo(
            feature="artifacts",
            state=CapabilityState.DISABLED_BY_CONFIG,
            message="Artifacts are disabled in the server configuration.",
            details={"enabled": enabled},
        )
    return CapabilityInfo(
        feature="artifacts",
        state=CapabilityState.AVAILABLE,
        message="Artifacts are enabled.",
        details={"enabled": enabled},
    )


def _eval_plexiscribe(artifacts: Dict[str, Any]) -> CapabilityInfo:
    enabled = artifacts.get("enabled", True)
    ps = artifacts.get("plexiscribe", {}) or {}
    ps_enabled = ps.get("enabled", True)
    if enabled is False or ps_enabled is False:
        return CapabilityInfo(
            feature="plexiscribe",
            state=CapabilityState.DISABLED_BY_CONFIG,
            message="Plexiscribe is disabled in the server configuration.",
            details={"artifacts_enabled": enabled, "plexiscribe_enabled": ps_enabled},
        )
    if not _has_feature("plexiscribe"):
        return CapabilityInfo(
            feature="plexiscribe",
            state=CapabilityState.DISABLED_BY_LICENSE,
            message="Plexiscribe requires a license.",
            details={},
        )
    return CapabilityInfo(
        feature="plexiscribe",
        state=CapabilityState.AVAILABLE,
        message="Plexiscribe is enabled.",
        details={"plexiscribe_enabled": ps_enabled},
    )


def _eval_plexiscript(artifacts: Dict[str, Any]) -> CapabilityInfo:
    enabled = artifacts.get("enabled", True)
    psc = artifacts.get("plexiscript", {}) or {}
    psc_enabled = psc.get("enabled", True)
    if enabled is False or psc_enabled is False:
        return CapabilityInfo(
            feature="plexiscript",
            state=CapabilityState.DISABLED_BY_CONFIG,
            message="Plexiscript is disabled in the server configuration.",
            details={"artifacts_enabled": enabled, "plexiscript_enabled": psc_enabled},
        )
    if not _has_feature("plexiscript"):
        return CapabilityInfo(
            feature="plexiscript",
            state=CapabilityState.DISABLED_BY_LICENSE,
            message="Plexiscript requires a license.",
            details={},
        )
    return CapabilityInfo(
        feature="plexiscript",
        state=CapabilityState.AVAILABLE,
        message="Plexiscript is enabled.",
        details={"plexiscript_enabled": psc_enabled},
    )


def _eval_plexiboard(artifacts: Dict[str, Any]) -> CapabilityInfo:
    enabled = artifacts.get("enabled", True)
    wb = artifacts.get("whiteboard", {}) or {}
    wb_enabled = wb.get("enabled", True)
    if enabled is False or wb_enabled is False:
        return CapabilityInfo(
            feature="plexiboard",
            state=CapabilityState.DISABLED_BY_CONFIG,
            message="Plexiboard (whiteboard) is disabled in the server configuration.",
            details={"artifacts_enabled": enabled, "whiteboard_enabled": wb_enabled},
        )
    if not _has_feature("artifacts_whiteboard"):
        return CapabilityInfo(
            feature="plexiboard",
            state=CapabilityState.DISABLED_BY_LICENSE,
            message="Whiteboard requires a license.",
            details={},
        )
    return CapabilityInfo(
        feature="plexiboard",
        state=CapabilityState.AVAILABLE,
        message="Plexiboard is enabled.",
        details={"whiteboard_enabled": wb_enabled},
    )


def _eval_whiteboard(artifacts: Dict[str, Any]) -> CapabilityInfo:
    enabled = artifacts.get("enabled", True)
    whiteboard = artifacts.get("whiteboard", {}) or {}
    wb_enabled = whiteboard.get("enabled", False)
    if enabled is False or wb_enabled is False:
        return CapabilityInfo(
            feature="artifacts_whiteboard",
            state=CapabilityState.DISABLED_BY_CONFIG,
            message=("Whiteboard artifacts are disabled in the server configuration."),
            details={"artifacts_enabled": enabled, "whiteboard_enabled": wb_enabled},
        )
    if not _has_feature("artifacts_whiteboard"):
        return CapabilityInfo(
            feature="artifacts_whiteboard",
            state=CapabilityState.DISABLED_BY_LICENSE,
            message=(
                "Whiteboard artifacts require a license that enables the "
                "'artifacts_whiteboard' feature."
            ),
            details={"licensed_feature": "artifacts_whiteboard"},
        )
    return CapabilityInfo(
        feature="artifacts_whiteboard",
        state=CapabilityState.AVAILABLE,
        message="Whiteboard artifacts are available.",
        details={},
    )


def _eval_voice_transcription(artifacts: Dict[str, Any]) -> CapabilityInfo:
    enabled = artifacts.get("enabled", True)
    voice = artifacts.get("voice", {}) or {}
    transcription = voice.get("transcription", {}) or {}
    tr_enabled = transcription.get("enabled", False)
    if enabled is False or tr_enabled is False:
        return CapabilityInfo(
            feature="voice_transcription",
            state=CapabilityState.DISABLED_BY_CONFIG,
            message=("Voice transcription is disabled in the server configuration."),
            details={
                "artifacts_enabled": enabled,
                "transcription_enabled": tr_enabled,
            },
        )
    if not _has_feature("voice_transcription"):
        return CapabilityInfo(
            feature="voice_transcription",
            state=CapabilityState.DISABLED_BY_LICENSE,
            message=(
                "Voice transcription requires a license that enables the "
                "'voice_transcription' feature."
            ),
            details={"licensed_feature": "voice_transcription"},
        )

    provider = transcription.get("provider", "local_whisper")
    details: Dict[str, Any] = {"provider": provider}
    if provider in ("local_whisper", "openai", "azure"):
        # The provider factory is the single source of truth for whether a
        # backend is usable. A ValueError means the config is internally
        # inconsistent (missing key, unknown provider) → misconfigured. For
        # cloud providers the factory also validates the required credentials;
        # for local_whisper it validates the import + model size.
        try:
            from src.core.artifacts.transcription.provider import (
                get_transcription_provider,
            )

            prov = get_transcription_provider(transcription)
        except ValueError as exc:
            return CapabilityInfo(
                feature="voice_transcription",
                state=CapabilityState.MISCONFIGURED,
                message=str(exc),
                details=details,
            )
        if not prov.is_available():
            # Local whisper reports dependency-missing only when the module is
            # absent or the model size is invalid; cloud providers report a
            # missing key via the factory above, so reaching here with
            # local_whisper means the import failed.
            if provider == "local_whisper":
                return CapabilityInfo(
                    feature="voice_transcription",
                    state=CapabilityState.DEPENDENCY_MISSING,
                    message=(
                        "Whisper selected but not installed — install with: "
                        "pip install openai-whisper"
                    ),
                    details=details,
                )
            return CapabilityInfo(
                feature="voice_transcription",
                state=CapabilityState.MISCONFIGURED,
                message=(
                    f"{provider} transcription is not available with the "
                    "current configuration."
                ),
                details=details,
            )
        return CapabilityInfo(
            feature="voice_transcription",
            state=CapabilityState.AVAILABLE,
            message=f"Voice transcription ({provider}) is available.",
            details=details,
        )
    return CapabilityInfo(
        feature="voice_transcription",
        state=CapabilityState.MISCONFIGURED,
        message=f"Unknown transcription provider: '{provider}'.",
        details=details,
    )


def _eval_voice_recording(artifacts: Dict[str, Any]) -> CapabilityInfo:
    enabled = artifacts.get("enabled", True)
    voice = artifacts.get("voice", {}) or {}
    allow_recording = voice.get("allow_recording", True)
    if enabled is False or allow_recording is False:
        return CapabilityInfo(
            feature="voice_recording",
            state=CapabilityState.DISABLED_BY_CONFIG,
            message="Voice recording is disabled in the server configuration.",
            details={
                "artifacts_enabled": enabled,
                "allow_recording": allow_recording,
            },
        )
    return CapabilityInfo(
        feature="voice_recording",
        state=CapabilityState.AVAILABLE,
        message="Voice recording is enabled.",
        details={"allow_recording": allow_recording},
    )


def get_artifact_capabilities(
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, CapabilityInfo]:
    """
    Evaluate the availability state of every artifacts feature.

    Args:
        config: Optional pre-loaded artifacts config dict. When ``None`` the
            configuration is loaded via the standard config accessor.

    Returns:
        A dict keyed by feature name with a :class:`CapabilityInfo` per feature.
        Never raises.
    """
    try:
        artifacts = _load_config(config)
        return {
            "artifacts": _eval_artifacts(artifacts),
            "artifacts_whiteboard": _eval_whiteboard(artifacts),
            "voice_transcription": _eval_voice_transcription(artifacts),
            "voice_recording": _eval_voice_recording(artifacts),
            "plexiscribe": _eval_plexiscribe(artifacts),
            "plexiscript": _eval_plexiscript(artifacts),
            "plexiboard": _eval_plexiboard(artifacts),
        }
    except Exception:
        # Last-resort fallback so the endpoint can never crash.
        return {
            "artifacts": CapabilityInfo(
                feature="artifacts",
                state=CapabilityState.MISCONFIGURED,
                message="Unable to evaluate artifacts capabilities.",
                details={},
            )
        }


def artifact_type_capability(feature_type: Any) -> str:
    """Return the capability name that gates an artifact type/value."""
    value = getattr(feature_type, "value", feature_type)
    return {
        "whiteboard": "artifacts_whiteboard",
        "plexiscribe": "plexiscribe",
        "plexiscript": "plexiscript",
    }.get(str(value), "artifacts")


def capability_allows_artifact(
    feature_type: Any, config: Optional[Dict[str, Any]] = None
) -> bool:
    """Return whether the artifact type is currently available."""
    return (
        get_capability(artifact_type_capability(feature_type), config).state
        == CapabilityState.AVAILABLE
    )


def get_capability(
    feature: str, config: Optional[Dict[str, Any]] = None
) -> CapabilityInfo:
    """
    Return the :class:`CapabilityInfo` for a single feature.

    Unknown features resolve to a ``MISCONFIGURED`` state with a helpful
    message. Never raises.
    """
    capabilities = get_artifact_capabilities(config)
    info = capabilities.get(feature)
    if info is None:
        return CapabilityInfo(
            feature=feature,
            state=CapabilityState.MISCONFIGURED,
            message=f"Unknown capability feature: '{feature}'.",
            details={},
        )
    return info


def capability_to_dict(info: CapabilityInfo) -> Dict[str, Any]:
    """Serialize a :class:`CapabilityInfo` to a plain JSON-compatible dict."""
    return {
        "feature": info.feature,
        "state": info.state.value,
        "message": info.message,
        "details": info.details,
    }


__all__ = [
    "CapabilityState",
    "CapabilityInfo",
    "get_artifact_capabilities",
    "get_capability",
    "artifact_type_capability",
    "capability_allows_artifact",
    "capability_to_dict",
]
