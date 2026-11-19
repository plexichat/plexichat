"""Regression: thread-slowmode reads must fail closed without a view gate.

``GET /threads/{thread_id}/slowmode`` used to wrap its entire thread-view
check in ``if threads is not None``. When the threads module could not be
resolved, the view gate was skipped and ``ThreadSlowmode.get_slowmode`` was
queried for any caller-supplied id — so any authenticated user could read the
slowmode configuration of a thread they cannot view (including a private
thread they are not a member of). The sibling channel read and both write
paths already refuse to run without their checker; this pins the same
fail-closed contract for the thread read.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

USER = SimpleNamespace(user_id=123, token_type="user")
THREAD_ID = "1234567890123456789"


def _patch_common(monkeypatch, threads):
    import src.api as api

    monkeypatch.setattr(api, "get_db", lambda: SimpleNamespace())
    monkeypatch.setattr(api, "get_threads", lambda: threads)


def _fake_threads(can_view):
    return SimpleNamespace(can_view_thread=lambda user_id, tid: can_view)


@pytest.mark.asyncio
async def test_missing_threads_module_denies_read(monkeypatch):
    import src.api.routes.features.slowmode as mod

    _patch_common(monkeypatch, None)

    with pytest.raises(HTTPException) as exc:
        await mod.get_thread_slowmode(THREAD_ID, current_user=USER)
    # A missing dependency is a server error, never an unauthenticated read.
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_non_viewable_thread_denies_read(monkeypatch):
    import src.api.routes.features.slowmode as mod

    _patch_common(monkeypatch, _fake_threads(False))

    with pytest.raises(HTTPException) as exc:
        await mod.get_thread_slowmode(THREAD_ID, current_user=USER)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_failing_view_check_denies_read(monkeypatch):
    import src.api.routes.features.slowmode as mod

    def _boom(user_id, tid):
        raise RuntimeError("threads backend down")

    _patch_common(
        monkeypatch, SimpleNamespace(can_view_thread=_boom)
    )

    with pytest.raises(HTTPException) as exc:
        await mod.get_thread_slowmode(THREAD_ID, current_user=USER)
    # A checker that raises cannot prove access — fail closed to 404, not 500.
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_viewable_thread_still_succeeds(monkeypatch):
    import src.api.routes.features.slowmode as mod
    import src.core.threads.slowmode as slowmode_mod

    _patch_common(monkeypatch, _fake_threads(True))

    class _Slowmode:
        def __init__(self, db):
            pass

        def get_slowmode(self, tid):
            return {"thread_id": str(tid), "interval_ms": 0}

    monkeypatch.setattr(slowmode_mod, "ThreadSlowmode", _Slowmode)

    result = await mod.get_thread_slowmode(THREAD_ID, current_user=USER)
    assert result["slowmode"]["interval_ms"] == 0
