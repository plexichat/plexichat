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

from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, Optional

from src.core.bounded_lru import BoundedLRU


class CapabilityState(str, Enum):
    """Availability state for a single capability/feature."""

    AVAILABLE = "available"
    EVALUATION = "evaluation"
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


def _copy_capability_info(info: CapabilityInfo) -> CapabilityInfo:
    """Return an independent copy of a memoized capability verdict.

    ``CapabilityInfo`` is a mutable dataclass whose ``details`` field is a
    mutable dict. The no-config cache in :func:`get_capability` and
    :func:`get_artifact_capabilities` is shared by every request and keyed only
    by feature name, so handing out the cached instance by reference would let
    one caller's in-place mutation (flipping ``state`` to bypass a license gate,
    or editing ``details``) silently rewrite the verdict every later caller
    sees for the rest of the TTL. Copy on both the store and read paths so the
    stored object is never reachable by a caller.
    """
    return replace(info, details=deepcopy(info.details))


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


def _evaluation_window(artifacts: Dict[str, Any]) -> tuple[bool, int]:
    """Return ``(enabled, ttl_days)`` for the license-free evaluation fallback.

    Evaluation mode lets a deployment try a licensed feature without a license;
    content created during evaluation is treated as ephemeral and cleared by the
    evaluation cleanup job after ``ttl_days``. It is opt-in and defaults off.
    """
    evaluation = artifacts.get("evaluation", {}) or {}
    enabled = bool(evaluation.get("enabled", False))
    try:
        ttl_days = max(1, int(evaluation.get("ttl_days", 30)))
    except (TypeError, ValueError):
        ttl_days = 30
    return enabled, ttl_days


def _license_or_eval(
    feature: str, label: str, artifacts: Dict[str, Any]
) -> CapabilityInfo:
    """Resolve an unlicensed, config-enabled feature to eval or disabled.

    Callers only reach this helper after confirming the feature is unlicensed.
    When evaluation mode is enabled the feature becomes usable in
    ``EVALUATION`` state (and is therefore subject to auto-expiry); otherwise
    it stays ``DISABLED_BY_LICENSE``.
    """
    eval_enabled, ttl_days = _evaluation_window(artifacts)
    if eval_enabled:
        return CapabilityInfo(
            feature=feature,
            state=CapabilityState.EVALUATION,
            message=(
                f"{label} is running in evaluation mode and will be "
                f"automatically cleared after {ttl_days} days."
            ),
            details={"evaluation": True, "ttl_days": ttl_days},
        )
    return CapabilityInfo(
        feature=feature,
        state=CapabilityState.DISABLED_BY_LICENSE,
        message=f"{label} requires a license.",
        details={"licensed_feature": feature},
    )


def evaluation_ttl_days(config: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Return the evaluation retention window (days), or ``None``.

    Suite/artifact content created while the license-free evaluation fallback
    is active is ephemeral: it is purged by the retention cleanup job after
    this many days. Returns ``None`` when evaluation mode is off or the
    deployment holds the master ``artifacts`` license feature (licensed
    deployments keep their normal retention policy).
    """
    artifacts = _load_config(config)
    enabled, ttl_days = _evaluation_window(artifacts)
    if not enabled:
        return None
    if _has_feature("artifacts"):
        return None
    return ttl_days


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
        return _license_or_eval("plexiscribe", "Plexiscribe", artifacts)
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
        return _license_or_eval("plexiscript", "Plexiscript", artifacts)
    return CapabilityInfo(
        feature="plexiscript",
        state=CapabilityState.AVAILABLE,
        message="Plexiscript is enabled.",
        details={"plexiscript_enabled": psc_enabled},
    )


def _eval_plexiboard(artifacts: Dict[str, Any]) -> CapabilityInfo:
    """Return the same verdict as the whiteboard capability.

    ``plexiboard`` is an alias for ``artifacts_whiteboard``: the Suite
    manifest and ``artifact_type_capability`` both reference the latter. A
    single evaluator keeps the two keys from ever disagreeing (previously
    ``plexiboard`` defaulted to enabled while ``artifacts_whiteboard``
    defaulted to disabled, so the admin panel could show contradictory
    states for the same feature).
    """
    info = _eval_whiteboard(artifacts)
    if info.state == CapabilityState.AVAILABLE:
        message = "Plexiboard is enabled."
    elif info.state == CapabilityState.EVALUATION:
        message = "Plexiboard is running in evaluation mode."
    elif info.state == CapabilityState.DISABLED_BY_LICENSE:
        message = "Whiteboard requires a license."
    else:
        message = "Plexiboard (whiteboard) is disabled in the server configuration."
    return CapabilityInfo(
        feature="plexiboard",
        state=info.state,
        message=message,
        details=dict(info.details),
    )


def _eval_whiteboard(artifacts: Dict[str, Any]) -> CapabilityInfo:
    enabled = artifacts.get("enabled", True)
    whiteboard = artifacts.get("whiteboard", {}) or {}
    wb_enabled = whiteboard.get("enabled", True)
    if enabled is False or wb_enabled is False:
        return CapabilityInfo(
            feature="artifacts_whiteboard",
            state=CapabilityState.DISABLED_BY_CONFIG,
            message=("Whiteboard artifacts are disabled in the server configuration."),
            details={"artifacts_enabled": enabled, "whiteboard_enabled": wb_enabled},
        )
    if not _has_feature("artifacts_whiteboard"):
        return _license_or_eval("artifacts_whiteboard", "Whiteboard", artifacts)
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


def _eval_suite_feature(
    artifacts: Dict[str, Any], feature: str, label: str
) -> CapabilityInfo:
    """Evaluate a Suite domain capability using the artifacts license boundary."""
    enabled = artifacts.get("enabled", True)
    suite_config = artifacts.get(feature, {}) or {}
    feature_enabled = suite_config.get("enabled", True)
    if enabled is False or feature_enabled is False:
        return CapabilityInfo(
            feature=feature,
            state=CapabilityState.DISABLED_BY_CONFIG,
            message=f"{label} is disabled in the server configuration.",
            details={"artifacts_enabled": enabled, "feature_enabled": feature_enabled},
        )
    if not _has_feature(feature):
        return _license_or_eval(feature, label, artifacts)
    return CapabilityInfo(
        feature=feature,
        state=CapabilityState.AVAILABLE,
        message=f"{label} is enabled.",
        details={"feature_enabled": feature_enabled},
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


def _evaluators() -> Dict[str, Callable[[Dict[str, Any]], CapabilityInfo]]:
    """Return the per-feature evaluator registry.

    Kept as a function so ``get_capability`` can evaluate a single feature
    without paying for the others (notably the voice-transcription provider
    import, which is unnecessary for Suite/artifact capability checks).
    """
    return {
        "artifacts": _eval_artifacts,
        "artifacts_whiteboard": _eval_whiteboard,
        "voice_transcription": _eval_voice_transcription,
        "voice_recording": _eval_voice_recording,
        "plexiscribe": _eval_plexiscribe,
        "plexiscript": _eval_plexiscript,
        "plexiboard": _eval_plexiboard,
        "plexistash": lambda artifacts: _eval_suite_feature(
            artifacts, "plexistash", "PlexiStash"
        ),
        "plexisum": lambda artifacts: _eval_suite_feature(
            artifacts, "plexisum", "PlexiSum"
        ),
    }


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

    With ``config=None`` the per-feature verdicts come from the same bounded,
    short-TTL LRU that :func:`get_capability` uses. Both launcher and admin
    catalog routes build this full map (and both are warmed at startup), so a
    cold evaluation used to run every evaluator twice -- including the
    voice-transcription provider import and backend probe. Reusing the bounded
    cache collapses the second build to LRU hits. An explicit ``config`` still
    bypasses the cache, so callers/tests always get a fresh evaluation for the
    config they supplied.
    """
    try:
        artifacts = _load_config(config)
        result: Dict[str, CapabilityInfo] = {}
        for feature, evaluate in _evaluators().items():
            if config is None:
                cached = _capability_cache.get(feature)
                if cached is not None:
                    result[feature] = _copy_capability_info(cached)
                    continue
            info = evaluate(artifacts)
            if config is None:
                _capability_cache.set(feature, info)
                result[feature] = _copy_capability_info(info)
            else:
                result[feature] = info
        return result
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


def is_usable(state: CapabilityState) -> bool:
    """Return whether a capability state permits use (available or evaluation).

    Every feature gate should route through this helper so the evaluation
    fallback behaves identically to a licensed deployment.
    """
    return state in (CapabilityState.AVAILABLE, CapabilityState.EVALUATION)


def capability_allows_artifact(
    feature_type: Any, config: Optional[Dict[str, Any]] = None
) -> bool:
    """Return whether the artifact type is currently usable."""
    return is_usable(
        get_capability(artifact_type_capability(feature_type), config).state
    )


# Process-local memoization for the no-config hot path. Suite routes and
# capability gates call ``get_capability(feature)`` on every request; the
# underlying license/config lookups are already cached, but re-running the
# evaluator (and, for voice transcription, the provider availability probe)
# on each call is still avoidable work.
#
# The cache is a bounded LRU (entry + byte cap + TTL), not a plain dict. A bare
# ``dict`` kept every feature name ever evaluated forever: TTL was only
# checked on access, so a capability that stopped being asked about (a renamed
# artifact, a removed suite manifest) stayed resident indefinitely, and a
# flood of distinct feature strings could grow the process without limit.
# A short TTL still bounds staleness after a license hot-swap/config reload;
# ``clear_capability_cache`` remains the authoritative full-invalidation hook
# used by the licensing admin routes. Eviction never weakens the gate: a
# missing entry is recomputed from the live license/config, and unknown or
# failing features resolve fail-closed to MISCONFIGURED.
_CAPABILITY_CACHE_TTL_SECONDS = 5.0
_CAPABILITY_CACHE_MAX_ENTRIES = 256
_CAPABILITY_CACHE_MAX_BYTES = 256 * 1024


def _capability_info_size(info: CapabilityInfo) -> int:
    """Approximate footprint of one memoized capability verdict."""
    return (
        len(str(info.feature).encode("utf-8", "ignore"))
        + len(str(info.message).encode("utf-8", "ignore"))
        + len(str(info.details).encode("utf-8", "ignore"))
        + 64
    )


_capability_cache = BoundedLRU(
    max_entries=_CAPABILITY_CACHE_MAX_ENTRIES,
    max_bytes=_CAPABILITY_CACHE_MAX_BYTES,
    ttl_seconds=_CAPABILITY_CACHE_TTL_SECONDS,
    size_of=_capability_info_size,
)


def clear_capability_cache() -> None:
    """Drop memoized capability results (call after a license/config change).

    This is a full invalidation: after a hot-swap no cached AVAILABLE verdict
    may gate a feature that the new license has disabled.
    """
    _capability_cache.clear()


def get_capability(
    feature: str, config: Optional[Dict[str, Any]] = None
) -> CapabilityInfo:
    """
    Return the :class:`CapabilityInfo` for a single feature.

    Evaluates only the requested feature (rather than the whole catalog) so
    hot paths like the Suite route capability gates don't import the voice
    transcription provider on every request. Unknown features resolve to a
    ``MISCONFIGURED`` state with a helpful message. Never raises.

    When ``config`` is omitted the result is memoized for a few seconds
    (see ``_CAPABILITY_CACHE_TTL_SECONDS``); explicit configs bypass the
    cache so callers and tests always get a freshly-evaluated result.
    """
    if config is None:
        cached = _capability_cache.get(feature)
        if cached is not None:
            return _copy_capability_info(cached)
    try:
        artifacts = _load_config(config)
        evaluator = _evaluators().get(feature)
        if evaluator is None:
            info = CapabilityInfo(
                feature=feature,
                state=CapabilityState.MISCONFIGURED,
                message=f"Unknown capability feature: '{feature}'.",
                details={},
            )
        else:
            info = evaluator(artifacts)
    except Exception:
        info = CapabilityInfo(
            feature=feature,
            state=CapabilityState.MISCONFIGURED,
            message="Unable to evaluate the requested capability.",
            details={},
        )
    if config is None:
        _capability_cache.set(feature, info)
        return _copy_capability_info(info)
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
    "clear_capability_cache",
    "artifact_type_capability",
    "capability_allows_artifact",
    "is_usable",
    "evaluation_ttl_days",
    "capability_to_dict",
]
