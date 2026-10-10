"""
Tests for the artifacts capability service.

Covers:
- CapabilityState enum and CapabilityInfo serialization
- Config loading (supplied dict vs global config vs failure)
- Per-feature state evaluation: artifacts, whiteboard, voice
  transcription/recording, plexiscribe, plexiscript, plexiboard,
  plexistash, plexisum
- License-gated features (disabled by license)
- Misconfiguration paths (unknown provider, unknown feature)
- capability_to_dict / get_capability / artifact_type_capability /
  capability_allows_artifact helpers
- Never-raises guarantees
"""

from src.core.artifacts.capabilities import (
    CapabilityState,
    CapabilityInfo,
    get_artifact_capabilities,
    get_capability,
    artifact_type_capability,
    capability_allows_artifact,
    capability_to_dict,
)


# =============================================================================
# Core helpers
# =============================================================================


class TestCapabilityState:
    """Test the capability state enum."""

    def test_states_have_string_values(self):
        """Every state must serialize to a stable string."""
        assert CapabilityState.AVAILABLE.value == "available"
        assert CapabilityState.DISABLED_BY_CONFIG.value == "disabled_by_config"
        assert CapabilityState.DISABLED_BY_LICENSE.value == "disabled_by_license"
        assert CapabilityState.DEPENDENCY_MISSING.value == "dependency_missing"
        assert CapabilityState.MISCONFIGURED.value == "misconfigured"

    def test_to_dict_roundtrip(self):
        """capability_to_dict must expose all four fields."""
        info = CapabilityInfo(
            feature="artifacts",
            state=CapabilityState.AVAILABLE,
            message="ok",
            details={"enabled": True},
        )
        d = capability_to_dict(info)
        assert d == {
            "feature": "artifacts",
            "state": "available",
            "message": "ok",
            "details": {"enabled": True},
        }


# =============================================================================
# get_artifact_capabilities
# =============================================================================


class TestArtifactCapabilities:
    """Test full capability evaluation."""

    def test_default_config_all_available(self):
        """With no config, artifacts must be available."""
        caps = get_artifact_capabilities({})
        # The suite-level artifacts gate is on by default.
        assert caps["artifacts"].state == CapabilityState.AVAILABLE
        # Whiteboard artifacts default to enabled, so without a license they
        # are disabled by license (not config).
        assert caps["artifacts_whiteboard"].state == CapabilityState.DISABLED_BY_LICENSE
        # Voice recording is on by default.
        assert caps["voice_recording"].state == CapabilityState.AVAILABLE
        # License-gated features fall back to disabled_by_license without a
        # valid license feature flag.
        assert caps["plexiscribe"].state == CapabilityState.DISABLED_BY_LICENSE
        assert caps["plexistash"].state == CapabilityState.DISABLED_BY_LICENSE
        assert caps["plexisum"].state == CapabilityState.DISABLED_BY_LICENSE

    def test_artifacts_disabled_by_config(self):
        """Disabling the whole artifacts suite must cascade."""
        caps = get_artifact_capabilities({"enabled": False})
        assert caps["artifacts"].state == CapabilityState.DISABLED_BY_CONFIG
        assert caps["artifacts_whiteboard"].state == CapabilityState.DISABLED_BY_CONFIG
        assert caps["voice_transcription"].state == CapabilityState.DISABLED_BY_CONFIG
        assert caps["voice_recording"].state == CapabilityState.DISABLED_BY_CONFIG
        assert caps["plexiscribe"].state == CapabilityState.DISABLED_BY_CONFIG
        assert caps["plexiboard"].state == CapabilityState.DISABLED_BY_CONFIG

    def test_all_features_present(self):
        """The capability map must cover every documented feature."""
        caps = get_artifact_capabilities({})
        expected = {
            "artifacts",
            "artifacts_whiteboard",
            "voice_transcription",
            "voice_recording",
            "plexiscribe",
            "plexiscript",
            "plexiboard",
            "plexistash",
            "plexisum",
        }
        assert set(caps.keys()) == expected

    def test_never_raises_on_garbage(self):
        """Garbage config must not raise — degrades to the fallback map."""
        caps = get_artifact_capabilities({"enabled": "not-a-bool", "voice": "x"})
        # A config error degrades the whole evaluation to the MISCONFIGURED
        # fallback for the artifacts gate.
        assert caps["artifacts"].state == CapabilityState.MISCONFIGURED


# =============================================================================
# Individual feature evaluation
# =============================================================================


class TestFeatureEvaluation:
    """Test per-feature state transitions."""

    def test_whiteboard_enabled_flag(self):
        """Whiteboard honors its own enabled flag."""
        caps = get_artifact_capabilities({"whiteboard": {"enabled": False}})
        assert caps["artifacts_whiteboard"].state == CapabilityState.DISABLED_BY_CONFIG

    def test_voice_transcription_enabled(self, monkeypatch):
        """Voice transcription must be available with a valid provider + license."""

        class _Prov:
            def is_available(self):
                return True

        import src.core.artifacts.transcription.provider as provider_mod
        import src.core.artifacts.capabilities as caps_mod

        monkeypatch.setattr(
            provider_mod, "get_transcription_provider", lambda cfg: _Prov()
        )
        monkeypatch.setattr(
            caps_mod, "_has_feature", lambda feature, default=False: True
        )
        caps = get_artifact_capabilities(
            {"voice": {"transcription": {"enabled": True, "provider": "openai"}}}
        )
        assert caps["voice_transcription"].state == CapabilityState.AVAILABLE
        assert caps["voice_transcription"].details["provider"] == "openai"

    def test_voice_transcription_disabled_by_config(self, monkeypatch):
        """Transcription disabled by flag must be DISABLED_BY_CONFIG."""
        import src.core.artifacts.capabilities as caps_mod

        monkeypatch.setattr(
            caps_mod, "_has_feature", lambda feature, default=False: True
        )
        caps = get_artifact_capabilities(
            {"voice": {"transcription": {"enabled": False}}}
        )
        assert caps["voice_transcription"].state == CapabilityState.DISABLED_BY_CONFIG

    def test_voice_transcription_unknown_provider(self, monkeypatch):
        """An unknown provider must be MISCONFIGURED once the license passes."""
        import src.core.artifacts.capabilities as caps_mod

        monkeypatch.setattr(
            caps_mod, "_has_feature", lambda feature, default=False: True
        )
        caps = get_artifact_capabilities(
            {"voice": {"transcription": {"enabled": True, "provider": "bogus"}}}
        )
        assert caps["voice_transcription"].state == CapabilityState.MISCONFIGURED

    def test_voice_recording_flag(self):
        """Recording honors allow_recording."""
        caps = get_artifact_capabilities({"voice": {"allow_recording": False}})
        assert caps["voice_recording"].state == CapabilityState.DISABLED_BY_CONFIG

    def test_whiteboard_enabled_via_config(self, monkeypatch):
        """Whiteboard enabled + licensed must be AVAILABLE."""
        import src.core.artifacts.capabilities as caps_mod

        monkeypatch.setattr(
            caps_mod, "_has_feature", lambda feature, default=False: True
        )
        caps = get_artifact_capabilities({"whiteboard": {"enabled": True}})
        assert caps["artifacts_whiteboard"].state == CapabilityState.AVAILABLE

    def test_plexiscribe_enabled_flag(self):
        """Plexiscribe disabled by its own flag must be DISABLED_BY_CONFIG."""
        caps = get_artifact_capabilities({"plexiscribe": {"enabled": False}})
        assert caps["plexiscribe"].state == CapabilityState.DISABLED_BY_CONFIG

    def test_plexiboard_enabled_flag(self):
        """Plexiboard disabled by its own flag must be DISABLED_BY_CONFIG."""
        caps = get_artifact_capabilities({"whiteboard": {"enabled": False}})
        assert caps["plexiboard"].state == CapabilityState.DISABLED_BY_CONFIG

    def test_plexistash_and_sum_config(self):
        """Suite features honor their own enabled flags."""
        caps = get_artifact_capabilities({"plexistash": {"enabled": False}})
        assert caps["plexistash"].state == CapabilityState.DISABLED_BY_CONFIG
        caps = get_artifact_capabilities({"plexisum": {"enabled": False}})
        assert caps["plexisum"].state == CapabilityState.DISABLED_BY_CONFIG


# =============================================================================
# get_capability / artifact_type_capability / capability_allows_artifact
# =============================================================================


class TestCapabilityHelpers:
    """Test single-capability lookup helpers."""

    def test_get_capability_known(self):
        """Known features must resolve."""
        info = get_capability("artifacts", {})
        assert info.feature == "artifacts"
        assert info.state in (
            CapabilityState.AVAILABLE,
            CapabilityState.DISABLED_BY_CONFIG,
        )

    def test_get_capability_unknown(self):
        """Unknown features must resolve to MISCONFIGURED, not raise."""
        info = get_capability("does_not_exist", {})
        assert info.state == CapabilityState.MISCONFIGURED
        assert "does_not_exist" in info.message

    def test_get_capability_only_evaluates_requested_feature(self, monkeypatch):
        """``get_capability`` must not evaluate unrelated features.

        Hot paths like the Suite capability gates ask for a single feature;
        evaluating the whole catalog would import the voice-transcription
        provider on every request. A single-feature lookup must never invoke
        the voice evaluator.
        """
        import src.core.artifacts.capabilities as caps_mod

        calls: list = []

        def tracking_voice(artifacts):
            calls.append("voice_transcription")
            return CapabilityInfo(
                feature="voice_transcription",
                state=CapabilityState.AVAILABLE,
                message="ok",
                details={},
            )

        monkeypatch.setattr(caps_mod, "_eval_voice_transcription", tracking_voice)

        info = get_capability("artifacts", {})
        assert info.feature == "artifacts"
        assert calls == []

        # Requesting the voice feature itself still invokes its evaluator.
        voice = get_capability(
            "voice_transcription", {"voice": {"transcription": {"enabled": True}}}
        )
        assert voice.feature == "voice_transcription"
        assert calls == ["voice_transcription"]

    def test_artifact_type_capability_mapping(self):
        """Artifact types must map to capability names."""
        assert artifact_type_capability("whiteboard") == "artifacts_whiteboard"
        assert artifact_type_capability("plexiscribe") == "plexiscribe"
        assert artifact_type_capability("plexiscript") == "plexiscript"
        assert artifact_type_capability("anything_else") == "artifacts"

    def test_capability_allows_artifact_enabled(self, monkeypatch):
        """Enabled + licensed config must allow whiteboard artifacts."""
        import src.core.artifacts.capabilities as caps_mod

        monkeypatch.setattr(
            caps_mod, "_has_feature", lambda feature, default=False: True
        )
        assert (
            capability_allows_artifact("whiteboard", {"whiteboard": {"enabled": True}})
            is True
        )

    def test_capability_allows_artifact_disabled(self):
        """Disabled config must reject whiteboard artifacts."""
        assert (
            capability_allows_artifact("whiteboard", {"whiteboard": {"enabled": False}})
            is False
        )

    def test_capability_allows_artifact_default(self):
        """Unknown artifact types resolve to the artifacts gate."""
        assert capability_allows_artifact("mystery", {}) is True


# =============================================================================
# License gating
# =============================================================================


class TestLicenseGating:
    """Test license-based gating paths."""

    def test_license_feature_gate(self):
        """A licensed feature flag must flip state to AVAILABLE."""
        import src.core.artifacts.capabilities as caps_mod

        orig = caps_mod._has_feature
        caps_mod._has_feature = lambda feature, default=False: True
        try:
            caps = get_artifact_capabilities({})
            assert caps["plexiscribe"].state == CapabilityState.AVAILABLE
            assert caps["plexistash"].state == CapabilityState.AVAILABLE
            assert caps["plexisum"].state == CapabilityState.AVAILABLE
        finally:
            caps_mod._has_feature = orig

    def test_license_check_never_raises(self):
        """A failing license check must not crash — degrades to the fallback."""
        import src.core.artifacts.capabilities as caps_mod

        orig = caps_mod._has_feature

        def boom(feature, default=False):
            raise RuntimeError("license server down")

        caps_mod._has_feature = boom
        try:
            caps = get_artifact_capabilities({})
            # The evaluation fails over to the single-feature MISCONFIGURED map.
            assert caps["artifacts"].state == CapabilityState.MISCONFIGURED
            assert set(caps.keys()) == {"artifacts"}
        finally:
            caps_mod._has_feature = orig


# =============================================================================
# Evaluation mode (license-free trial)
# =============================================================================


class TestEvaluationMode:
    """License-free evaluation mode gates + retention fallback."""

    def test_eval_state_when_unlicensed(self):
        """Unlicensed + evaluation enabled -> EVALUATION state, not disabled."""
        caps = get_artifact_capabilities(
            {"evaluation": {"enabled": True, "ttl_days": 7}}
        )
        assert caps["plexiscribe"].state == CapabilityState.EVALUATION
        assert caps["plexiscript"].state == CapabilityState.EVALUATION
        assert caps["plexistash"].state == CapabilityState.EVALUATION
        assert caps["plexisum"].state == CapabilityState.EVALUATION
        assert caps["artifacts_whiteboard"].state == CapabilityState.EVALUATION
        assert caps["plexiboard"].state == CapabilityState.EVALUATION
        assert caps["plexiscribe"].details["ttl_days"] == 7

    def test_eval_off_keeps_disabled(self):
        """Without evaluation enabled, unlicensed features stay disabled."""
        caps = get_artifact_capabilities({})
        assert caps["plexiscribe"].state == CapabilityState.DISABLED_BY_LICENSE

    def test_eval_licensed_stays_available(self, monkeypatch):
        """A license overrides evaluation mode back to AVAILABLE."""
        import src.core.artifacts.capabilities as caps_mod

        monkeypatch.setattr(
            caps_mod, "_has_feature", lambda feature, default=False: True
        )
        caps = get_artifact_capabilities({"evaluation": {"enabled": True}})
        assert caps["plexiscribe"].state == CapabilityState.AVAILABLE

    def test_is_usable(self):
        """is_usable accepts available + evaluation, rejects the rest."""
        from src.core.artifacts.capabilities import is_usable

        assert is_usable(CapabilityState.AVAILABLE) is True
        assert is_usable(CapabilityState.EVALUATION) is True
        assert is_usable(CapabilityState.DISABLED_BY_LICENSE) is False
        assert is_usable(CapabilityState.DISABLED_BY_CONFIG) is False
        assert is_usable(CapabilityState.MISCONFIGURED) is False

    def test_capability_allows_artifact_in_eval(self):
        """Evaluation mode must allow whiteboard artifacts."""
        assert (
            capability_allows_artifact("whiteboard", {"evaluation": {"enabled": True}})
            is True
        )

    def test_evaluation_ttl_days_off(self):
        """No eval -> no retention window."""
        from src.core.artifacts.capabilities import evaluation_ttl_days

        assert evaluation_ttl_days({}) is None
        assert evaluation_ttl_days({"evaluation": {"enabled": False}}) is None

    def test_evaluation_ttl_days_on(self, monkeypatch):
        """Eval enabled + unlicensed -> ttl retention window."""
        from src.core.artifacts.capabilities import evaluation_ttl_days
        import src.core.artifacts.capabilities as caps_mod

        monkeypatch.setattr(
            caps_mod, "_has_feature", lambda feature, default=False: False
        )
        assert (
            evaluation_ttl_days({"evaluation": {"enabled": True, "ttl_days": 15}}) == 15
        )

    def test_evaluation_ttl_days_licensed(self, monkeypatch):
        """A licensed deployment ignores the evaluation retention window."""
        from src.core.artifacts.capabilities import evaluation_ttl_days
        import src.core.artifacts.capabilities as caps_mod

        monkeypatch.setattr(
            caps_mod, "_has_feature", lambda feature, default=False: True
        )
        assert (
            evaluation_ttl_days({"evaluation": {"enabled": True, "ttl_days": 15}})
            is None
        )


# =============================================================================
# Config loading
# =============================================================================


class TestConfigLoading:
    """Test _load_config behavior."""

    def test_supplied_config_wins(self):
        """An explicitly supplied config must be used verbatim."""
        from src.core.artifacts.capabilities import _load_config

        conf = {"enabled": False}
        assert _load_config(conf) is conf

    def test_load_from_global(self):
        """Without a supplied config, the global config must be read."""
        from src.core.artifacts.capabilities import _load_config
        from utils.config import _config_instance as loader

        loader.config["artifacts"] = {"enabled": False}
        loader._invalidate_cache()
        try:
            assert _load_config(None) == {"enabled": False}
        finally:
            loader.config.pop("artifacts", None)
            loader._invalidate_cache()

    def test_load_handles_missing(self):
        """A missing artifacts section must yield an empty dict."""
        from src.core.artifacts.capabilities import _load_config
        from utils.config import _config_instance as loader

        loader.config.pop("artifacts", None)
        loader._invalidate_cache()
        try:
            assert _load_config(None) == {}
        finally:
            loader._invalidate_cache()


# =============================================================================
# Whiteboard alias + memoization
# =============================================================================


class TestWhiteboardAlias:
    """``plexiboard`` and ``artifacts_whiteboard`` are one gate."""

    def test_alias_matches_disabled(self):
        caps = get_artifact_capabilities({"whiteboard": {"enabled": False}})
        assert caps["plexiboard"].state == caps["artifacts_whiteboard"].state
        assert caps["plexiboard"].state == CapabilityState.DISABLED_BY_CONFIG

    def test_alias_matches_enabled(self, monkeypatch):
        import src.core.artifacts.capabilities as caps_mod

        monkeypatch.setattr(
            caps_mod, "_has_feature", lambda feature, default=False: True
        )
        caps = get_artifact_capabilities({"whiteboard": {"enabled": True}})
        assert caps["plexiboard"].state == CapabilityState.AVAILABLE
        assert caps["artifacts_whiteboard"].state == CapabilityState.AVAILABLE

    def test_alias_matches_unlicensed(self):
        # Empty config: whiteboard defaults to enabled, so without a license
        # both keys resolve to the same disabled_by_license verdict.
        caps = get_artifact_capabilities({})
        assert caps["plexiboard"].state == caps["artifacts_whiteboard"].state
        assert caps["artifacts_whiteboard"].state == CapabilityState.DISABLED_BY_LICENSE


class TestCapabilityMemoization:
    """The no-config ``get_capability`` path memoizes for a short TTL."""

    def test_memoizes_and_clears(self, monkeypatch):
        import src.core.artifacts.capabilities as caps_mod

        caps_mod.clear_capability_cache()
        calls = {"n": 0}
        real_evaluators = caps_mod._evaluators

        def counting():
            calls["n"] += 1
            return real_evaluators()

        monkeypatch.setattr(caps_mod, "_evaluators", counting)

        first = caps_mod.get_capability("artifacts")
        second = caps_mod.get_capability("artifacts")
        assert first.feature == second.feature
        # The second call is served from the cache without re-evaluating.
        assert calls["n"] == 1

        caps_mod.clear_capability_cache()
        caps_mod.get_capability("artifacts")
        assert calls["n"] == 2

    def test_explicit_config_bypasses_cache(self, monkeypatch):
        import src.core.artifacts.capabilities as caps_mod

        caps_mod.clear_capability_cache()
        calls = {"n": 0}
        real_evaluators = caps_mod._evaluators

        def counting():
            calls["n"] += 1
            return real_evaluators()

        monkeypatch.setattr(caps_mod, "_evaluators", counting)
        caps_mod.get_capability("artifacts", {})
        caps_mod.get_capability("artifacts", {})
        # Explicit configs are always freshly evaluated.
        assert calls["n"] == 2
