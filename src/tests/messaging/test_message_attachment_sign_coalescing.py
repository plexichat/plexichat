"""Coalesce repeated attachment URL signing on message listings.

BEFORE: ``_message_to_response`` called ``media_mod.sign_url()`` once for every
media attachment on every message of a list page. A 100-message page carrying
the same shared image on each message paid 100 HMAC-SHA256 computations, 100
``urlparse``/``urlencode`` round trips and 100 ``time.time()`` reads for what is
a single deterministic ``(url, file_id, user_id, expires_in)`` result.

AFTER: identical signing requests within one ASGI request are coalesced at the
``UrlSigner`` layer through the bounded request-local memo the rest of the read
path already uses. The key embeds the viewer id, so a URL signed for one
credential can never be replayed to another, and the memo never outlives the
request.
"""

import pytest

from src.core.database.cache.request_local import request_cache_scope
from src.core.media.security.signing import UrlSigner

SECRET = "a-stable-test-signing-secret-not-a-default"
PATH = "/api/v1/media/attachments/shared.png"


def _count_generated(monkeypatch, signer):
    """Wrap the actual HMAC generator and count distinct signature work."""
    real = signer._generate_signature
    calls = {"n": 0}

    def counting(data):
        calls["n"] += 1
        return real(data)

    monkeypatch.setattr(signer, "_generate_signature", counting)
    return calls


@pytest.mark.messaging
def test_repeated_signing_is_coalesced_within_a_request(monkeypatch):
    signer = UrlSigner(SECRET)
    calls = _count_generated(monkeypatch, signer)

    with request_cache_scope():
        signed = [signer.sign_url(PATH, 42, user_id=7) for _ in range(100)]

    # 100 identical sign requests collapse to one HMAC computation.
    assert calls["n"] == 1
    # Every caller receives a valid, viewer-bound signature.
    assert len({s.signature for s in signed}) == 1
    assert all(s.file_id == 42 and s.url.startswith(PATH) for s in signed)
    assert signer.verify_url(signed[0].url, current_user_id=7)[0] is True


@pytest.mark.messaging
def test_without_coalescing_layer_hmac_runs_once_per_call(monkeypatch):
    """Before the fix (or when the memo is unavailable), every call signs."""
    import src.core.media.security.signing as signing_mod

    signer = UrlSigner(SECRET)
    calls = _count_generated(monkeypatch, signer)

    # Bypass the coalescing layer to measure the original work: 20 attachments
    # on a listing page used to mean 20 HMAC computations.
    monkeypatch.setattr(
        signing_mod,
        "coalesced_cache_get",
        lambda key, loader: loader(),
    )
    for _ in range(20):
        signer.sign_url(PATH, 42, user_id=7)

    assert calls["n"] == 20, "baseline: one HMAC per signer call"


@pytest.mark.messaging
def test_signatures_are_partitioned_by_viewer_and_inputs(monkeypatch):
    """The coalescing key must never let one input tuple reuse another's URL."""
    signer = UrlSigner(SECRET)
    calls = _count_generated(monkeypatch, signer)

    with request_cache_scope():
        signer.sign_url(PATH, 42, user_id=7)
        signer.sign_url(PATH, 42, user_id=8)  # different viewer
        signer.sign_url(PATH, 99, user_id=7)  # different file
        signer.sign_url(PATH, 42, user_id=7, expires_in=60)  # different expiry
        signer.sign_url("/other.png", 42, user_id=7)  # different path
        signer.sign_url(PATH, 42, user_id=7)  # repeat of the first

    assert calls["n"] == 5, "five distinct inputs -> five HMAC computations"


@pytest.mark.messaging
def test_coalesced_urls_are_independent_objects(monkeypatch):
    """A cached SignedUrl must not be shareable/mutable across callers."""
    signer = UrlSigner(SECRET)
    _count_generated(monkeypatch, signer)

    with request_cache_scope():
        a = signer.sign_url(PATH, 42, user_id=7)
        b = signer.sign_url(PATH, 42, user_id=7)

    assert a is not b
    a.url = "mutated"
    assert b.url != "mutated"
