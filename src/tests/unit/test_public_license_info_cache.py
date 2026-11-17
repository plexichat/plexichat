"""The public /license/info payload is cached and stays secret-free.

The endpoint is an unauthenticated reachability check. Its fields come only
from license state, so a burst of calls must collapse to one computation while
still refreshing after the TTL and never disclosing key material.
"""

import asyncio

import src.api.routes.public_license as pl
import src.utils as utils


_LICENSE_FIELDS = {
    "is_free_tier": False,
    "is_valid": True,
    "is_provisional": False,
    "get_license_type": "standard",
    "get_allowed_hosts": ["127.0.0.1"],
    "get_instance_id": "inst-1",
}


def _patch_licensing(monkeypatch, calls):
    for name, value in _LICENSE_FIELDS.items():

        def fake(_name=name, _value=value):
            calls.append(_name)
            return _value

        monkeypatch.setattr(utils.licensing, name, fake)
    monkeypatch.setattr(pl, "_fingerprint", lambda: "deadbeef")


def test_info_is_cached_within_ttl(monkeypatch):
    calls = []
    _patch_licensing(monkeypatch, calls)
    monkeypatch.setattr(pl, "_INFO_CACHE_TTL_SECONDS", 60.0)
    pl._clear_info_cache()

    first = asyncio.run(pl.license_info())
    second = asyncio.run(pl.license_info())

    assert first == second
    # Every license accessor ran exactly once across both calls.
    assert calls == list(_LICENSE_FIELDS)


def test_info_refreshes_after_ttl(monkeypatch):
    calls = []
    _patch_licensing(monkeypatch, calls)
    monkeypatch.setattr(pl, "_INFO_CACHE_TTL_SECONDS", 0.0)
    pl._clear_info_cache()

    asyncio.run(pl.license_info())
    asyncio.run(pl.license_info())

    assert calls == list(_LICENSE_FIELDS) * 2


def test_cached_info_is_detached_from_callers(monkeypatch):
    calls = []
    _patch_licensing(monkeypatch, calls)
    monkeypatch.setattr(pl, "_INFO_CACHE_TTL_SECONDS", 60.0)
    pl._clear_info_cache()

    first = asyncio.run(pl.license_info())
    first["allowed_hosts"].append("attacker.example")
    second = asyncio.run(pl.license_info())

    assert second["allowed_hosts"] == ["127.0.0.1"]
    assert calls == list(_LICENSE_FIELDS)


def test_info_payload_never_contains_secrets(monkeypatch):
    calls = []
    _patch_licensing(monkeypatch, calls)
    monkeypatch.setattr(pl, "_INFO_CACHE_TTL_SECONDS", 60.0)
    pl._clear_info_cache()

    result = asyncio.run(pl.license_info())

    assert set(result) == {
        "available",
        "free_tier",
        "valid",
        "provisional",
        "license_type",
        "allowed_hosts",
        "has_instance_id",
        "public_key_fingerprint",
    }
    assert result["has_instance_id"] is True
