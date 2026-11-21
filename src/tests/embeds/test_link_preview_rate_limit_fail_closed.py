"""The link-preview rate limiter must not fail open without a rowcount.

``LinkPreviewService._consume_rate_limit`` uses a conditional upsert so the
database itself enforces the per-user budget atomically. A driver without a
reliable ``rowcount`` takes a fallback that re-reads the counter; that fallback
used to accept ``request_count <= max_requests``. At the ceiling this grants a
request whose increment was *refused* by the ``WHERE request_count < max``
clause -- one free preview past the budget on every such driver, even when the
window was already exhausted.

These tests pin the boundary: an exhausted budget is denied, a budget with room
is still allowed, and the normal rowcount path is unchanged.
"""

from types import SimpleNamespace

from src.core.embeds.link_preview import LinkPreviewService


_MAX = 5
_USER = 4242

_MISSING = object()


class _FakeDb:
    """Minimal DB double: one canned counter row and a configurable rowcount."""

    def __init__(self, request_count, rowcount=_MISSING):
        self.request_count = request_count
        self.rowcount = rowcount
        self.cleanup_deletes = 0
        self.consume_sql = None

    def execute(self, query, params=None):
        if "DELETE FROM embed_preview_rate_limits" in query:
            self.cleanup_deletes += 1
            return SimpleNamespace(rowcount=0)
        self.consume_sql = query
        if self.rowcount is _MISSING:
            # A driver that does not expose rowcount at all.
            return object()
        return SimpleNamespace(rowcount=self.rowcount)

    def fetch_one(self, query, params=None):
        return {"request_count": self.request_count}


def _service(db):
    service = LinkPreviewService(db)
    service._config["rate_limit_requests"] = _MAX
    service._config["rate_limit_window_seconds"] = 60
    return service


def test_missing_rowcount_attr_denies_exhausted_budget():
    """No rowcount at all: a counter already at the ceiling must deny."""
    db = _FakeDb(request_count=_MAX)
    assert _service(db)._consume_rate_limit(_USER) is False


def test_none_rowcount_denies_exhausted_budget():
    """``rowcount = None`` is the same unknown-outcome case and must deny."""
    db = _FakeDb(request_count=_MAX, rowcount=None)
    assert _service(db)._consume_rate_limit(_USER) is False


def test_none_rowcount_denies_over_budget():
    """A counter past the ceiling (config changed mid-window) also denies."""
    db = _FakeDb(request_count=_MAX + 1, rowcount=None)
    assert _service(db)._consume_rate_limit(_USER) is False


def test_fallback_still_allows_within_budget():
    """The conservative boundary must not turn the limiter into a blanket deny."""
    db = _FakeDb(request_count=_MAX - 1, rowcount=None)
    assert _service(db)._consume_rate_limit(_USER) is True


def test_rowcount_path_unchanged():
    """Drivers with a reliable rowcount keep the existing contract."""
    assert (
        _service(_FakeDb(request_count=_MAX, rowcount=0))._consume_rate_limit(_USER)
        is False
    )
    assert (
        _service(_FakeDb(request_count=1, rowcount=1))._consume_rate_limit(_USER)
        is True
    )


def test_atomic_guard_is_still_in_the_sql():
    """The fix must not weaken the database-side conditional consume."""
    db = _FakeDb(request_count=_MAX - 1, rowcount=None)
    _service(db)._consume_rate_limit(_USER)
    assert "request_count < ?" in db.consume_sql
