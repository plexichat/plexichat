"""One-time download tickets must be claimed BEFORE the signed URL (grant) is minted.

The redeem route used to ``resolve_download_ticket`` (a non-consuming read),
sign a media URL, and only then call ``redeem_download_ticket`` (the atomic
``redeemed_at IS NULL`` CAS). Signing IS the side effect: it mints a short-lived
bearer capability that grants download access. Because the claim came after the
sign, two concurrent redemptions of the SAME ticket both reached ``sign_url``
before either CAS ran -- both produced a usable signed download grant from a
credential that is supposed to be strictly single-use.

This is the same ``verify-then-side-effect-without-atomic-claim`` shape the
chunked-upload redemption fix (81cfd505) closed: the single-winner transition
must happen before the expensive side effect. The fix claims the ticket first
and signs only for the winner; a signing failure then fails closed (the ticket
is already burned) instead of leaving a one-time grant unmintable-but-spent.

The behavioural test forces the losing interleaving deterministically with a
barrier in the (pre-fix) resolve step, then asserts that the grant side effect
ran exactly once. It fails on the old ordering and passes on the new one, so it
is non-vacuous.
"""

from __future__ import annotations

import asyncio
import threading
import types

import pytest

from src.api.routes.suite.stash import grants
from src.api.schemas.stash import StashDownloadRedeemRequest

USER = types.SimpleNamespace(user_id=42)
TICKET = "t" * 32
MEDIA_REF = "opaque-media-ref"


class _AtomicTicketManager:
    """Manager double whose ticket redemption is a real single-winner CAS."""

    def __init__(self, resolve_barrier: threading.Barrier | None):
        self._resolve_barrier = resolve_barrier
        self._lock = threading.Lock()
        self._redeemed = False
        self.resolve_calls = 0
        self.redeem_calls = 0

    def resolve_download_ticket(self, user_id, ticket):
        # Only exercised by the buggy (sign-before-claim) ordering. Forcing
        # both racers through here before either can claim makes the double
        # sign deterministic instead of timing-dependent.
        self.resolve_calls += 1
        if self._resolve_barrier is not None:
            self._resolve_barrier.wait(timeout=5)
        return {
            "item_id": "123",
            "version_id": None,
            "media_object_id": MEDIA_REF,
            "expires_at": 4102444800000,
        }

    def redeem_download_ticket(self, user_id, ticket):
        self.redeem_calls += 1
        with self._lock:
            if self._redeemed:
                raise PermissionError(
                    "Download ticket is invalid, expired, or already redeemed"
                )
            self._redeemed = True
        return {
            "item_id": "123",
            "version_id": None,
            "media_object_id": MEDIA_REF,
            "expires_at": 4102444800000,
        }


class _CountingMedia:
    def __init__(self):
        self.sign_calls = 0
        self._lock = threading.Lock()

    def get_file_by_filename(self, name):
        return types.SimpleNamespace(id=7)

    def get_file(self, file_id):
        return types.SimpleNamespace(id=7)

    def sign_url(self, file_id, *, expires_in, user_id):
        with self._lock:
            self.sign_calls += 1
        return types.SimpleNamespace(url="https://signed.example/7")


def _run_redeem(monkeypatch, manager, media):
    # Isolate the redemption from the online-guess budget: this test is about
    # the claim/sign ordering, not the rate limiter.
    monkeypatch.setattr(grants, "_enforce_download_ticket_redeem_guess_limit", lambda *a, **k: None)
    monkeypatch.setattr(grants, "_manager", lambda: manager)
    monkeypatch.setattr(grants.api, "get_media", lambda: media)
    body = StashDownloadRedeemRequest(ticket=TICKET)
    return asyncio.run(grants.redeem_download_ticket(body, USER, None))


def test_concurrent_redemptions_mint_exactly_one_signed_grant(monkeypatch):
    barrier = threading.Barrier(2)
    manager = _AtomicTicketManager(resolve_barrier=barrier)
    media = _CountingMedia()

    outcomes = []
    errors = []

    def _worker():
        try:
            outcomes.append(_run_redeem(monkeypatch, manager, media))
        except Exception as exc:  # the losing racer must fail closed
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a redemption deadlocked"

    # Only the single winner may receive a signed download capability.
    granted = [o for o in outcomes if getattr(o, "media_url", None)]
    assert len(granted) == 1, f"expected one signed grant, got {len(granted)}"
    assert len(errors) == 1, f"the losing racer must fail closed, got {len(errors)}"
    # The critical invariant: the expensive side effect (signing) ran once.
    assert media.sign_calls == 1, (
        f"one-time ticket produced {media.sign_calls} signed grants "
        "(claim did not precede the side effect)"
    )
    assert manager.redeem_calls == 2


def test_route_source_claims_ticket_before_signing(monkeypatch):
    """Structural pin: the claim must appear before sign_url in the route body."""
    import ast
    import inspect

    source = inspect.getsource(grants.redeem_download_ticket)
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
    call_lines = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            call_lines.setdefault(node.func.attr, node.lineno)
    claim = call_lines.get("redeem_download_ticket")
    sign = call_lines.get("sign_url")
    assert claim is not None and sign is not None, "route lost claim or sign call"
    assert claim < sign, (
        f"ticket is claimed (line {claim}) after the signed grant is minted "
        f"(line {sign})"
    )
