"""Deterministic clock for TOTP tests.

``pyotp`` reads the wall clock twice in a 2FA flow: once when a test mints a
code with ``TOTP.now()`` and again inside the server's ``TOTP.verify()``. Under
heavy host contention the gap between the two can cross the code's validity
window (30s interval, ±1 window), so an otherwise-correct code is rejected as
"Invalid code". Freezing pyotp's notion of "now" for the duration of a
mint-and-verify sequence removes that host-timing dependency.

Only pyotp's view of time is pinned; the TOTP replay cache still reads the real
``time`` module, so replay-window behaviour is unchanged.
"""

from __future__ import annotations

import contextlib
import datetime
from typing import Iterator, Optional

import pyotp.totp as _pyotp_totp

_fixed_instant: Optional[datetime.datetime] = None


class _FixedDateTime(datetime.datetime):
    """A ``datetime`` whose ``now()`` always returns one pinned instant.

    The returned value must be a ``_FixedDateTime`` instance: pyotp checks
    ``isinstance(for_time, datetime.datetime)`` against the patched class, so
    returning a plain ``datetime`` would send it down the integer-timestamp
    branch and raise.
    """

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        assert _fixed_instant is not None
        fixed = _FixedDateTime(
            _fixed_instant.year,
            _fixed_instant.month,
            _fixed_instant.day,
            _fixed_instant.hour,
            _fixed_instant.minute,
            _fixed_instant.second,
            _fixed_instant.microsecond,
        )
        if tz is None:
            return fixed
        return fixed.replace(tzinfo=datetime.timezone.utc).astimezone(tz)


class _DateTimeModule:
    """Stand-in for the ``datetime`` module with a pinned ``datetime`` class."""

    datetime = _FixedDateTime

    def __getattr__(self, name: str):
        return getattr(datetime, name)


@contextlib.contextmanager
def frozen_totp_time(
    instant: Optional[datetime.datetime] = None,
) -> Iterator[None]:
    """Pin ``pyotp.totp``'s clock so code generation and verification agree."""
    global _fixed_instant
    previous_datetime = _pyotp_totp.datetime
    previous_instant = _fixed_instant
    _fixed_instant = instant or datetime.datetime.now(datetime.timezone.utc).replace(
        tzinfo=None
    )
    _pyotp_totp.datetime = _DateTimeModule()
    try:
        yield
    finally:
        _pyotp_totp.datetime = previous_datetime
        _fixed_instant = previous_instant
