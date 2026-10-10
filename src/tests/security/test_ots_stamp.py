"""Tests for ``ci-tooling/ots_stamp.py``.

The stamper produces the OpenTimestamps proofs published on the ``ots-proofs``
branch, so its contract is narrow but load-bearing:

1. Attestation -- a proof file is only ever written once a calendar has
   merged an attestation onto the commit digest. If every calendar fails the
   tool must fail, and must not leave a hollow ``.ots`` behind for the
   ``ots:push_proofs`` job to commit.
2. Failover -- a failing calendar falls through to the next one instead of
   aborting the run.
3. Naming -- the proof is named for the project and the truncated commit SHA.

``opentimestamps`` is not a runtime or dev dependency (the CI job installs it
itself), so the calendar client is stubbed and the tool is loaded against the
stub modules.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ci-tooling" / "ots_stamp.py"

COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
PROJECT_PATH = "plexichat/plexichat"


class _FakeTimestamp:
    """Stand-in for ``opentimestamps.core.timestamp.Timestamp``."""

    def __init__(self, digest: bytes) -> None:
        self.digest = digest
        self.merged: list[object] = []

    def merge(self, other: object) -> None:
        self.merged.append(other)


class _FakeDetachedTimestampFile:
    """Stand-in for ``DetachedTimestampFile``; records what it serialised."""

    def __init__(self, op: object, timestamp: _FakeTimestamp) -> None:
        self.op = op
        self.timestamp = timestamp
        self.serialized = False

    def serialize(self, ctx: object) -> None:
        self.serialized = True
        ctx.fh.write(b"fake-proof-payload")


class _RecordingCalendar:
    """Calendar stub driven by a per-URL behaviour table."""

    # url -> "ok" or an Exception instance to raise
    behaviour: dict[str, object] = {}
    submissions: list[str] = []

    def __init__(self, url: str) -> None:
        self.url = url

    def submit(self, digest: bytes, timeout: int = 30) -> object:
        type(self).submissions.append(self.url)
        outcome = type(self).behaviour.get(self.url, "ok")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def tool(monkeypatch, tmp_path):
    """Load ``ots_stamp.py`` against stub ``opentimestamps`` modules."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CI_COMMIT_SHA", COMMIT_SHA)
    monkeypatch.setenv("CI_PROJECT_PATH", PROJECT_PATH)

    created: list[_FakeDetachedTimestampFile] = []

    def _make_timestamp(digest: bytes) -> _FakeTimestamp:
        return _FakeTimestamp(digest)

    def _make_dtf(op: object, timestamp: _FakeTimestamp) -> _FakeDetachedTimestampFile:
        dtf = _FakeDetachedTimestampFile(op, timestamp)
        created.append(dtf)
        return dtf

    stubs = {
        "opentimestamps": types.ModuleType("opentimestamps"),
        "opentimestamps.calendar": types.ModuleType("opentimestamps.calendar"),
        "opentimestamps.core": types.ModuleType("opentimestamps.core"),
        "opentimestamps.core.op": types.ModuleType("opentimestamps.core.op"),
        "opentimestamps.core.serialize": types.ModuleType(
            "opentimestamps.core.serialize"
        ),
        "opentimestamps.core.timestamp": types.ModuleType(
            "opentimestamps.core.timestamp"
        ),
    }
    stubs["opentimestamps.calendar"].RemoteCalendar = _RecordingCalendar
    stubs["opentimestamps.core.op"].OpSHA256 = object
    stubs["opentimestamps.core.serialize"].StreamSerializationContext = lambda fh: (
        types.SimpleNamespace(fh=fh)
    )
    stubs["opentimestamps.core.timestamp"].DetachedTimestampFile = _make_dtf
    stubs["opentimestamps.core.timestamp"].Timestamp = _make_timestamp

    for name, stub in stubs.items():
        monkeypatch.setitem(sys.modules, name, stub)

    _RecordingCalendar.behaviour = {}
    _RecordingCalendar.submissions = []

    spec = importlib.util.spec_from_file_location("ots_stamp", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "ots_stamp", module)
    spec.loader.exec_module(module)

    module._created_files = created  # type: ignore[attr-defined]
    return module


def _proofs(tmp_path: Path) -> list[Path]:
    out_dir = tmp_path / "ots-proofs"
    return sorted(out_dir.glob("*.ots")) if out_dir.is_dir() else []


def test_all_calendars_failing_writes_no_proof(tool, tmp_path):
    """Every calendar rejected the digest: fail, and write nothing."""
    _RecordingCalendar.behaviour = {
        url: RuntimeError("network unreachable")
        for url in (
            "https://ots.btc.catallaxy.com",
            "https://a.calendar.opentimestamps.org",
            "https://b.calendar.opentimestamps.org",
        )
    }

    assert tool.main() == 1
    assert _proofs(tmp_path) == []
    assert not (tmp_path / "ots-proofs").exists()
    # The run really did try every calendar rather than giving up early.
    assert len(_RecordingCalendar.submissions) == 3


def test_failing_calendar_falls_through_to_the_next(tool, tmp_path):
    """The first calendar is down; the second accepts and the proof lands."""
    _RecordingCalendar.behaviour = {
        "https://ots.btc.catallaxy.com": RuntimeError("calendar unavailable"),
        "https://a.calendar.opentimestamps.org": _FakeTimestamp(b"attestation"),
    }

    assert tool.main() == 0
    proofs = _proofs(tmp_path)
    assert [p.name for p in proofs] == [f"plexichat_plexichat_{COMMIT_SHA[:12]}.ots"]
    assert proofs[0].read_bytes() == b"fake-proof-payload"


def test_proof_carries_the_merged_attestation(tool, tmp_path):
    """A written proof is only ever serialised after a real merge."""
    attestation = _FakeTimestamp(b"attestation")
    _RecordingCalendar.behaviour = {
        url: attestation
        for url in (
            "https://ots.btc.catallaxy.com",
            "https://a.calendar.opentimestamps.org",
            "https://b.calendar.opentimestamps.org",
        )
    }

    assert tool.main() == 0

    created = tool._created_files
    assert len(created) == 1
    dtf = created[0]
    assert dtf.timestamp.merged == [attestation]
    assert dtf.serialized is True
    # The third calendar is not consulted once one has accepted.
    assert _RecordingCalendar.submissions == ["https://ots.btc.catallaxy.com"]
