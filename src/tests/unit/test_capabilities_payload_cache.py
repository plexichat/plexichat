"""The launcher capability catalog is memoized across requests.

Evaluating every capability (notably the voice-transcription provider probe)
is identical for all callers, so a burst of polls must not re-run it on each
request. The short TTL and explicit invalidation keep hot-swaps observable.
"""

import src.api.routes.capabilities as caps
from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState


def _info(name, state=CapabilityState.AVAILABLE):
    return CapabilityInfo(feature=name, state=state, message="ok", details={})


def test_payload_is_evaluated_once_within_ttl(monkeypatch):
    calls = []

    def fake_capabilities():
        calls.append(1)
        return {
            "editor": _info("editor"),
            "voice_transcription": _info(
                "voice_transcription", CapabilityState.DEPENDENCY_MISSING
            ),
        }

    monkeypatch.setattr(caps, "get_artifact_capabilities", fake_capabilities)
    caps.clear_capabilities_payload_cache()

    first = caps._get_capabilities_payload()
    second = caps._get_capabilities_payload()

    assert len(calls) == 1
    assert first == second
    assert first["editor"]["state"] == "available"
    assert first["voice_transcription"]["state"] == "dependency_missing"


def test_clear_forces_recomputation(monkeypatch):
    calls = []

    def fake_capabilities():
        calls.append(1)
        return {"editor": _info("editor")}

    monkeypatch.setattr(caps, "get_artifact_capabilities", fake_capabilities)
    caps.clear_capabilities_payload_cache()
    caps._get_capabilities_payload()
    caps.clear_capabilities_payload_cache()
    caps._get_capabilities_payload()

    assert len(calls) == 2


def test_filters_are_applied_to_the_cached_payload(monkeypatch):
    monkeypatch.setattr(
        caps,
        "get_artifact_capabilities",
        lambda: {"editor": _info("editor"), "whiteboard": _info("whiteboard")},
    )
    caps.clear_capabilities_payload_cache()

    assert set(caps._get_capabilities_payload()) == {"editor", "whiteboard"}
    filtered = caps.bound_capabilities(
        caps._get_capabilities_payload(), feature="editor"
    )
    assert set(filtered) == {"editor"}
