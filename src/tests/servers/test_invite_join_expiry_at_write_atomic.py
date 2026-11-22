"""Invite redemption must check expiry against the clock at WRITE time.

``use_invite`` guards consumption with a single atomic UPDATE that re-checks
``revoked``, ``max_uses`` and ``expires_at`` at write time.  It used to pass
that guard the *same* timestamp captured for the early validation checks, so
an invite that expired between validation and the guarded write was still
consumed and granted membership -- exactly the "passes validation, DB write
lands after expiry" window.  Uses/revoked were atomic; expiry was not.

These tests pin the two properties the guard must have:
  * the ``expires_at > ?`` predicate is evaluated with the write-time clock, so
    an invite already expired at the moment of consumption is refused; and
  * the single guarded UPDATE still caps concurrent consumption at max_uses.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from src.core.servers.exceptions import InviteExpiredError, InviteMaxUsesError
from src.core.servers.handlers.members.invites import MemberInviteMixin

pytestmark = pytest.mark.unit


class _ClockSequence:
    """Successive clock reads: validation-time first, then write-time."""

    def __init__(self, *values: int):
        self._values = list(values)
        self._idx = 0

    def __call__(self) -> int:
        value = self._values[min(self._idx, len(self._values) - 1)]
        self._idx += 1
        return value


class _Mgr:
    def __init__(self, clock: _ClockSequence):
        self._clock = clock
        self.audit: list[tuple] = []

    def _get_timestamp(self) -> int:
        return self._clock()

    def _log_audit(self, *args: Any, **kwargs: Any) -> None:
        self.audit.append((args, kwargs))


class _Cursor:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _FakeDB:
    """Faithful model of the single guarded UPDATE plus its refresh SELECT.

    The guarded UPDATE re-checks ``revoked`` / ``max_uses`` / ``expires_at``
    against the parameter the caller supplies, exactly like the real SQL.  A
    lock models the DB row-level serialisation that makes the guard
    authoritative under concurrency.
    """

    def __init__(self, row: dict):
        self.row = dict(row)
        self.consumed = False
        self.guard_now: int | None = None
        self._lock = threading.Lock()

    def fetch_one(self, sql: str, params: tuple) -> dict | None:
        with self._lock:
            code = params[0]
            if code != self.row["code"] or self.row["revoked"]:
                return None
            return dict(self.row)

    def execute(self, sql: str, params: tuple) -> _Cursor:
        with self._lock:
            if "uses = uses + 1" in sql:
                code, guard_now = params
                self.guard_now = guard_now
                expired = (
                    self.row["expires_at"] is not None
                    and self.row["expires_at"] <= guard_now
                )
                maxed = (
                    self.row["max_uses"] > 0
                    and self.row["uses"] >= self.row["max_uses"]
                )
                if self.row["revoked"] or expired or maxed:
                    return _Cursor(0)
                self.row["uses"] += 1
                self.consumed = True
                return _Cursor(1)
            if "uses - 1" in sql:
                self.row["uses"] = max(0, self.row["uses"] - 1)
                return _Cursor(1)
            return _Cursor(0)


class _Mixin(MemberInviteMixin):
    def __init__(self, db: _FakeDB, mgr: _Mgr):
        self.db = db
        self.manager = mgr
        self.joined: list[tuple] = []

    def add_member(self, server_id, user_id, inviter_id=None, temporary=False):
        self.joined.append((server_id, user_id, inviter_id, temporary))
        return ("member", server_id, user_id)


def _row(**overrides: Any) -> dict:
    row: dict = dict(
        id=1,
        code="EXPIRE1",
        server_id=500,
        channel_id=700,
        inviter_id=900,
        max_age=100,
        max_uses=1,
        uses=0,
        temporary=0,
        created_at=900,
        expires_at=1100,
        revoked=0,
    )
    row.update(overrides)
    return row


def test_invite_expired_at_write_is_not_consumed() -> None:
    """Validation at t=1000 (still live), write lands at t=1200 (expired).

    The guard's ``expires_at > ?`` must use 1200.  With the stale 1000 the
    invite is redeemed after it has expired, which is the bug this pins.
    """
    clock = _ClockSequence(1000, 1200)
    db = _FakeDB(_row())
    mixin = _Mixin(db, _Mgr(clock))

    with pytest.raises(InviteExpiredError):
        mixin.use_invite(user_id=42, code="EXPIRE1")

    assert db.guard_now == 1200  # non-vacuous: the write-time clock was used
    assert db.consumed is False  # the expired invite was NOT redeemed
    assert mixin.joined == []  # no membership granted after expiry
    assert db.row["uses"] == 0


def test_single_use_invite_not_over_consumed_concurrently() -> None:
    """The guarded UPDATE still caps a single-use invite at one redemption.

    Non-vacuous guard: if the atomic ``uses < max_uses`` clause is ever
    dropped for a SELECT-then-increment, more than one thread wins here.
    """
    db = _FakeDB(_row(max_uses=1, expires_at=None))
    mgr = _Mgr(_ClockSequence(1000))
    barrier = threading.Barrier(8)
    joined: list[int] = []
    rejected: list[int] = []
    errors: list[BaseException] = []

    def worker(uid: int) -> None:
        mixin = _Mixin(db, mgr)
        try:
            barrier.wait()
            mixin.use_invite(user_id=uid, code="EXPIRE1")
            joined.append(uid)
        except InviteMaxUsesError:
            rejected.append(uid)
        except BaseException as exc:  # pragma: no cover - surfaces unexpected
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(100 + i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(joined) == 1
    assert len(rejected) == 7
    assert db.row["uses"] == 1
