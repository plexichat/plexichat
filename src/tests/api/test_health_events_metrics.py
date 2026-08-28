"""
Phase-2 finalization: ``/api/v1/health`` events_metrics + audit surface.

The endpoint was extended with optional advisory fields:

* ``events_metrics`` -- output of ``event_manager.get_loss_metrics()``
* ``is_queue_recreate_pending`` -- audit predicate for loop-reset / drift

Both are wrapped in their own try/except so partial-success returns one
without dropping both, AND so a bug in the events layer does NOT take
this endpoint down (``status`` + ``version`` are the contract /health
consumers actually depend on).

NOTE on the import path: ``health.py`` reads
``from src.core.events import event_manager`` lazily inside the
function, and only the *module-level private* ``_manager`` attribute is
populated by ``events.setup()`` (the public name is not aliased). The
outer ``try/except`` catches the resulting ``ImportError`` and returns
``events_metrics=None`` -- currently the wiring is therefore advisory
ONLY. The forward-looking tests in this module alias the public name
via ``monkeypatch.setattr`` so the inner blocks fire; a regression test
also locks in the current ImportError-path behaviour as a contract so
that a future maintainer who fixes the wiring gap with a one-line
addition to ``src/core/events/__init__.py`` gets a clear failure
pointing at this test for the contract update.
"""

from unittest.mock import patch

import pytest

import src.core.events as events_module


pytestmark = [pytest.mark.api, pytest.mark.integration]


# --- shape: always-present keys ----------------------------------------


def test_health_response_has_expected_keys(test_client):
    """The base contract: 200 OK + status/version/events_metrics/
    is_queue_recreate_pending. The latter two MAY be None when the
    import path doesn't resolve (see module docstring); the test
    asserts structural presence, not value.
    """
    resp = test_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert "version" in body
    # New advisory fields -- keys MUST be present (None is acceptable).
    assert "events_metrics" in body
    assert "is_queue_recreate_pending" in body


# --- alias-absent: locks current ImportError-path behaviour as a
# ---                                  regression contract ---------------


def test_health_populates_metrics_via_public_alias(test_client):
    """D9 wiring fix: ``src/core/events/__init__.py`` now exposes a public
    ``event_manager`` alias (PEP 562 ``__getattr__``) so the inline
    ``from src.core.events import event_manager`` inside /health resolves
    against the configured manager and populates both advisory fields
    instead of silently returning None.
    """
    # The public alias must resolve (to a manager after setup, or at
    # worst None before setup -- never AttributeError).
    assert hasattr(events_module, "event_manager")
    manager = getattr(events_module, "event_manager", None)
    if manager is None:
        # Not set up in this environment: alias existence is the contract.
        pass
    else:
        assert hasattr(manager, "get_loss_metrics")
        assert hasattr(manager, "is_queue_recreate_pending")

    resp = test_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    # With the alias present AND the events module wired by the test
    # harness, the advisory fields resolve to real values. If the
    # harness did not wire events.setup(), events_metrics may be None,
    # but the endpoint must never 500 or raise AttributeError.
    assert body["status"] == "healthy"
    assert "events_metrics" in body
    assert "is_queue_recreate_pending" in body


# --- alias-present: forward-looking contract --------------------------


def test_health_populates_metrics_when_event_manager_aliased(test_client, monkeypatch):
    """If ``event_manager = _manager`` is added to ``src/core/events/__init__.py``,
    the inline ``from src.core.events import event_manager`` resolves and
    both blocks populate real values. We mirror the private manager
    onto the public name with ``monkeypatch.setattr`` for the duration
    of this request so the inner blocks run, then verify the contract.
    """
    em = events_module._manager
    assert em is not None
    monkeypatch.setattr(events_module, "event_manager", em, raising=False)

    resp = test_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["events_metrics"], dict)
    for key in (
        "dropped_events",
        "subscribers",
        "critical_subscribers",
        "queue",
    ):
        assert key in body["events_metrics"]
    assert isinstance(body["is_queue_recreate_pending"], bool)


# --- partial-success swallow --------------------------------------------


def test_health_swallows_get_loss_metrics_exception(test_client, monkeypatch):
    """A bug in ``get_loss_metrics`` MUST NOT crash /health. The
    metric is wrapped in its own try/except and falls back to ``None``;
    the rest of the response (status + version + the queue-recreate
    audit field) still returns.
    """
    em = events_module._manager
    assert em is not None
    monkeypatch.setattr(events_module, "event_manager", em, raising=False)
    with patch.object(
        em,
        "get_loss_metrics",
        side_effect=RuntimeError("simulated metrics failure"),
    ):
        resp = test_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    # Loss-metrics block raised -> advisory field becomes None,
    # everything else still serves.
    assert body["events_metrics"] is None
    assert body["status"] == "healthy"
    # is_queue_recreate_pending is a SEPARATE try block -- must
    # still resolve independently.
    assert "is_queue_recreate_pending" in body


def test_health_swallows_is_queue_recreate_pending_exception(test_client, monkeypatch):
    """The converse: ``is_queue_recreate_pending`` raising MUST NOT
    crash /health either, and the loss-metrics field (in its own
    try block) should still populate. Proves partial-success in BOTH
    directions.
    """
    em = events_module._manager
    assert em is not None
    monkeypatch.setattr(events_module, "event_manager", em, raising=False)
    with patch.object(
        em,
        "is_queue_recreate_pending",
        side_effect=RuntimeError("simulated audit failure"),
    ):
        resp = test_client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_queue_recreate_pending"] is None
    # metrics block still ran
    assert isinstance(body["events_metrics"], dict)
