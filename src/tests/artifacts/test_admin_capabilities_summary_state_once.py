"""Regression: the admin capability summary extracts each state once.

``_build_payload`` used to re-read ``capabilities[feature].state`` and re-convert
the enum for every comparison: once for ``available``, once for ``unavailable``,
and once per :class:`CapabilityState` member in ``by_state``. That is
O(states * features) redundant lookups over a map that cannot change during the
build. The summary now extracts every feature's state string in a single pass
and reuses it, producing a byte-identical payload.

The change is pure computation: no cache is added or widened, so bounded LRU,
non-recyclable DB identity, time-bound grants and fail-closed behavior are all
unaffected.
"""

import src.api.routes.admin.capabilities as ac
from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState


class _CountingInfo:
    """Wrap a CapabilityInfo to count ``.state`` reads by the builder."""

    def __init__(self, info: CapabilityInfo, counter: dict) -> None:
        self._info = info
        self._counter = counter

    @property
    def state(self):
        self._counter["state_reads"] += 1
        return self._info.state

    @property
    def feature(self):
        return self._info.feature

    @property
    def message(self):
        return self._info.message

    @property
    def details(self):
        return self._info.details


def _catalog(states) -> dict:
    return {
        f"feature_{i}": CapabilityInfo(
            feature=f"feature_{i}",
            state=state,
            message="ok",
            details={"enabled": True},
        )
        for i, state in enumerate(states)
    }


def _use(monkeypatch, catalog):
    monkeypatch.setattr(ac, "get_artifact_capabilities", lambda: catalog)
    ac.clear_admin_capabilities_payload_cache()
    return ac._build_payload()


def test_state_read_once_per_feature(monkeypatch):
    """One state read for serialization + one for the summary, per feature.

    Before the fix the summary alone issued ``2 + len(CapabilityState)`` reads
    per feature (available + unavailable + each by_state bucket), i.e. 9x the
    work for the current seven-state enum.
    """
    states = list(CapabilityState)
    counter = {"state_reads": 0}
    catalog = {
        name: _CountingInfo(info, counter) for name, info in _catalog(states).items()
    }

    payload = _use(monkeypatch, catalog)

    assert counter["state_reads"] == 2 * len(catalog)
    assert payload["summary"]["total"] == len(catalog)


def test_summary_is_byte_identical_to_original_logic(monkeypatch):
    """Reusing the extracted states must not change a single summary field."""
    states = list(CapabilityState)
    catalog = _catalog([states[i % len(states)] for i in range(11)])

    payload = _use(monkeypatch, catalog)

    # Independent implementation of the original per-comparison logic.
    def state_of(feature):
        state = catalog[feature].state
        return state.value if isinstance(state, CapabilityState) else state

    names = list(payload["capabilities"])
    available = [f for f in names if state_of(f) == CapabilityState.AVAILABLE.value]
    unavailable = [f for f in names if state_of(f) != CapabilityState.AVAILABLE.value]
    expected = {
        "total": len(names),
        "available_count": len(available),
        "unavailable_count": len(unavailable),
        "available": available,
        "unavailable": unavailable,
        "by_state": {
            state.value: [f for f in names if state_of(f) == state.value]
            for state in CapabilityState
        },
    }

    assert payload["summary"] == expected
