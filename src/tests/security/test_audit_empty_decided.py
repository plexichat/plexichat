"""Tests for sweep I: an authorization decision taken from a fetched listing.

Sweeps A-H ask whether a check *runs* and whether its result is *delivered*.
This sweep covers the shape fixed in ANNEX AB: the check ran, but its input was
a row of a listing the function itself had fetched, so the decision came from
the data instead of from the caller -- ``get_active_threads`` resolved
``channels.view`` from ``first_public.server_id``, where ``first_public`` was
pulled out of the very threads the guard had just counted.

The rule needs both halves: the guard folds a *collection*, and the access call
consumes a *row* of it. A scalar existence check (``if not row: return None``
before an access call on the fetched resource) is a normal fail-closed pattern
and must not be reported -- an earlier draft of this sweep reported 108 of
those and buried the two real sites.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_PATH = REPO_ROOT / "ci-tooling" / "audit_failopen.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("audit_empty_decided", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_empty_decided"] = module
    spec.loader.exec_module(module)
    return module


af = _load_tool()


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# The pre-fix listing: the accumulator is a listing built from a fetch, the
# guard folds it into a flag, and the row pulled out of it decides the check.
_PREFIX_SHAPE = """
class ThreadListingMixin:
    def get_active_threads(self, user_id, channel_id):
        rows = self._db.fetch_all("SELECT * FROM thread_threads", (channel_id,))
        active_threads = []
        for row in rows:
            active_threads.append(row)

        public_exists = any(t["type"] != "private" for t in active_threads)
        if public_exists and self._servers:
            first_public = next(
                t for t in active_threads if t["type"] != "private"
            )
            return self._check_permission(
                user_id, first_public["server_id"], "channels.view"
            )
        return []
"""

_ARCHIVED_SHAPE = """
class ThreadListingMixin:
    def get_archived_threads(self, user_id, channel_id):
        rows = self._db.fetch_all("SELECT * FROM thread_threads", (channel_id,))
        threads = [dict(row) for row in rows]
        if threads and self._servers:
            first = threads[0]
            return self._check_permission(user_id, first["server_id"], "channels.view")
        return []
"""

_SCALAR_PRESENCE = """
class MessagingService:
    def get_message(self, user_id, message_id):
        row = self._db.fetch_one("SELECT * FROM messages WHERE id = ?", (message_id,))
        if not row:
            return None
        conversation_id = row["conversation_id"]
        if not self.is_participant(user_id, conversation_id):
            return None
        return row
"""

_GUARD_WITHOUT_A_ROW = """
class BroadcastService:
    def broadcast(self, user_id, conversation_id):
        rows = self._db.fetch_all("SELECT * FROM members", (conversation_id,))
        if rows:
            self._check_permission(user_id, conversation_id, "messages.view")
        return [dict(row) for row in rows]
"""

_UP_FRONT_DECISION = """
class ThreadListingMixin:
    def get_active_threads(self, user_id, channel_id):
        has_view = self._channel_view_allowed(user_id, channel_id)
        rows = self._db.fetch_all("SELECT * FROM thread_threads", (channel_id,))
        threads = [dict(row) for row in rows]
        return [t for t in threads if has_view]
"""


def test_flags_the_check_decided_by_a_row_of_a_fetched_listing(tmp_path: Path) -> None:
    _write(tmp_path, "src/core/threads/manager/listing.py", _PREFIX_SHAPE)

    hits = af.sweep_empty_decided(tmp_path)

    assert len(hits) == 1, [h.detail for h in hits]
    assert hits[0].kind == af.EMPTY_DECIDED
    assert hits[0].symbol == "_check_permission"
    assert "first_public" in hits[0].detail
    assert "public_exists" in hits[0].detail


def test_flags_the_comprehension_variant(tmp_path: Path) -> None:
    """The archived path took the row by subscript instead of ``next``."""
    _write(tmp_path, "src/core/threads/manager/listing.py", _ARCHIVED_SHAPE)

    hits = af.sweep_empty_decided(tmp_path)

    assert len(hits) == 1
    assert "first" in hits[0].detail


def test_ignores_a_scalar_presence_check(tmp_path: Path) -> None:
    """``if not row: return None`` then authorize on the row is not this bug.

    The listing is a single fetched resource, not a collection whose contents
    decide the answer; gating on its existence fails closed.
    """
    _write(
        tmp_path, "src/core/messaging/services/message/retrieval.py", _SCALAR_PRESENCE
    )

    assert af.sweep_empty_decided(tmp_path) == []


def test_ignores_a_guard_whose_call_consumes_no_row(tmp_path: Path) -> None:
    """A listing guard around a call that authorizes the *argument* is fine."""
    _write(tmp_path, "src/core/messaging/services/broadcast.py", _GUARD_WITHOUT_A_ROW)

    assert af.sweep_empty_decided(tmp_path) == []


def test_ignores_a_decision_resolved_before_the_listing(tmp_path: Path) -> None:
    """The fixed shape: permission taken up front, listing only filters."""
    _write(tmp_path, "src/core/threads/manager/listing.py", _UP_FRONT_DECISION)

    assert af.sweep_empty_decided(tmp_path) == []


def test_skips_test_directories(tmp_path: Path) -> None:
    _write(tmp_path, "src/core/tests/test_listing.py", _PREFIX_SHAPE)

    assert af.sweep_empty_decided(tmp_path) == []


def test_runs_clean_on_this_tree() -> None:
    """The two sites ANNEX AB fixed must stay fixed."""
    hits = af.sweep_empty_decided(REPO_ROOT / "plexichat")

    assert hits == [], [f"{h.path}:{h.line} {h.detail}" for h in hits]


def test_the_class_is_reported_and_gates_the_exit_code(tmp_path: Path) -> None:
    """A hit must reach the report as a fail-open class, not just be returned."""
    server = tmp_path / "server"
    _write(server, "src/core/threads/manager/listing.py", _PREFIX_SHAPE)
    client = tmp_path / "client"
    client.mkdir()
    findings = tmp_path / "findings.md"
    findings.write_text("", encoding="utf-8")

    text, fail_open, missing = af.report(server, client, findings)

    assert "EMPTY-DECIDED (access decided from a fetched listing): 1" in text
    assert "## EMPTY-DECIDED" in text
    assert fail_open == 1
    assert missing == 0


@pytest.mark.parametrize(
    "shape", [_PREFIX_SHAPE, _ARCHIVED_SHAPE, _SCALAR_PRESENCE, _GUARD_WITHOUT_A_ROW]
)
def test_snippets_are_valid_python(shape: str) -> None:
    """Guard the fixtures themselves: an unparsable tree reports nothing."""
    import ast

    ast.parse(shape)
