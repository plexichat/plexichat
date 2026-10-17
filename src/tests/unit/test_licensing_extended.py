"""Extended targeted tests for the licensing core module."""

import base64
import json
import time

import pytest

from utils.licensing.core import (
    License,
    LicenseManager,
    InvalidLicenseError,
    generate_keypair,
    sign_license,
)


# ---------------------------------------------------------------------------
# License structure validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {"version": "", "instance_id": "i", "issued_at": 1, "expires_at": None},
            "version",
        ),
        (
            {"version": "1.0", "instance_id": "", "issued_at": 1, "expires_at": None},
            "Instance ID",
        ),
        (
            {"version": "1.0", "instance_id": "i", "issued_at": 0, "expires_at": None},
            "issued_at",
        ),
        (
            {"version": "1.0", "instance_id": "i", "issued_at": -5, "expires_at": None},
            "issued_at",
        ),
        (
            {"version": "1.0", "instance_id": "i", "issued_at": 100, "expires_at": 100},
            "Expiry",
        ),
        (
            {"version": "1.0", "instance_id": "i", "issued_at": 100, "expires_at": 99},
            "Expiry",
        ),
    ],
)
def test_license_structure_invalid(kwargs, match):
    with pytest.raises(InvalidLicenseError, match=match):
        License(**kwargs)


def test_license_structure_valid():
    lic = License(version="1.0", instance_id="i", issued_at=1, expires_at=None)
    assert lic.features == {}
    assert lic.limits == {}
    assert lic.signature is None
    assert lic.expires_at is None  # perpetual license


# ---------------------------------------------------------------------------
# Keypair + signing round-trip
# ---------------------------------------------------------------------------


def test_keypair_lengths():
    priv, pub = generate_keypair()
    assert len(priv) == 32
    assert len(pub) == 32


def test_sign_verify_round_trip():
    priv, pub = generate_keypair()
    data = {
        "version": "1.0",
        "instance_id": "instance-1",
        "issued_at": int(time.time()) - 100,
        "expires_at": int(time.time()) + 1000,
        "features": {"bond": True},
        "limits": {"max_users": 10},
    }
    sig = sign_license(priv, data)
    assert isinstance(sig, str)
    assert len(base64.b64decode(sig)) == 64

    lic = License(**data, signature=sig)
    assert lic.signature == sig
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict({**data, "signature": sig})
    result = mgr.validate()
    assert result.is_valid is True
    assert result.is_signature_valid is True
    assert result.is_expired is False
    assert result.instance_id == "instance-1"


def test_tampered_data_fails_signature():
    priv, pub = generate_keypair()
    data = {
        "version": "1.0",
        "instance_id": "instance-1",
        "issued_at": 1,
        "expires_at": None,
        "features": {"bond": False},
        "limits": {},
    }
    sig = sign_license(priv, data)
    tampered = {**data, "features": {"bond": True}}
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict({**tampered, "signature": sig})
    result = mgr.validate()
    assert result.is_valid is False
    assert result.is_signature_valid is False


def test_wrong_key_fails_signature():
    priv, _ = generate_keypair()
    _, other_pub = generate_keypair()
    data = {
        "version": "1.0",
        "instance_id": "i",
        "issued_at": 1,
        "expires_at": None,
        "features": {},
        "limits": {},
    }
    sig = sign_license(priv, data)
    mgr = LicenseManager(public_key_bytes=other_pub)
    mgr.load_from_dict({**data, "signature": sig})
    assert mgr.validate().is_valid is False


def test_missing_signature_fails():
    _, pub = generate_keypair()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(
        {"version": "1.0", "instance_id": "i", "issued_at": 1, "expires_at": None}
    )
    result = mgr.validate()
    assert result.is_valid is False
    assert result.error_message is not None


def test_no_public_key_fails_signature():
    priv, _ = generate_keypair()
    data = {
        "version": "1.0",
        "instance_id": "i",
        "issued_at": 1,
        "expires_at": None,
        "features": {},
        "limits": {},
    }
    sig = sign_license(priv, data)
    mgr = LicenseManager(public_key_bytes=None)
    # Hardcoded class key differs from the signing key -> invalid.
    mgr.load_from_dict({**data, "signature": sig})
    assert mgr.validate().is_valid is False


# ---------------------------------------------------------------------------
# Expiry handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("issued_delta", "expiry_delta", "expected_expired", "expected_valid"),
    [
        (-100, 1000, False, True),  # valid window
        (-100, -10, True, False),  # expired
        (
            -100,
            0,
            False,
            True,
        ),  # expiry == now: strict `now > expires_at`, not >=; boundary below
        (-100, None, False, True),  # perpetual
    ],
)
def test_expiry_window(
    issued_delta, expiry_delta, expected_expired, expected_valid, monkeypatch
):
    now = int(time.time())
    issued = now + issued_delta
    expires = None if expiry_delta is None else now + expiry_delta
    priv, pub = generate_keypair()
    data = {
        "version": "1.0",
        "instance_id": "i",
        "issued_at": issued,
        "expires_at": expires,
        "features": {},
        "limits": {},
    }
    data["signature"] = sign_license(priv, data)
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    result = mgr.validate()
    assert result.is_expired is expected_expired
    assert result.is_valid is expected_valid


def test_expiry_boundary_second_after():
    # A license expiring exactly now is NOT expired (uses >, not >=).
    now = int(time.time())
    _, pub = generate_keypair()
    data = {
        "version": "1.0",
        "instance_id": "i",
        "issued_at": now - 10,
        "expires_at": now,
        "features": {},
        "limits": {},
    }
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    assert mgr.validate().is_expired is False


# ---------------------------------------------------------------------------
# Loading paths
# ---------------------------------------------------------------------------


def test_load_from_dict_sets_source():
    mgr = LicenseManager()
    mgr.load_from_dict(
        {"version": "1.0", "instance_id": "i", "issued_at": 1, "expires_at": None}
    )
    assert mgr.get_license_source() == "dict"


def test_load_from_file_missing(tmp_path):
    mgr = LicenseManager()
    with pytest.raises(InvalidLicenseError, match="not found"):
        mgr.load_from_file(str(tmp_path / "nope.json"))


def test_load_from_file_malformed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    mgr = LicenseManager()
    with pytest.raises(InvalidLicenseError):
        mgr.load_from_file(str(p))


def test_load_from_file_valid(tmp_path):
    data = {"version": "1.0", "instance_id": "i", "issued_at": 1, "expires_at": None}
    p = tmp_path / "ok.json"
    p.write_text(json.dumps(data))
    mgr = LicenseManager()
    lic = mgr.load_from_file(str(p))
    assert lic.instance_id == "i"
    assert mgr.get_license_source() == f"file:{p}"


@pytest.mark.parametrize("payload", ["not-base64!!", "", "###"])
def test_load_from_base64_invalid(payload):
    mgr = LicenseManager()
    with pytest.raises(InvalidLicenseError):
        mgr.load_from_base64(payload)


def test_load_from_base64_valid():
    data = {"version": "1.0", "instance_id": "b64", "issued_at": 1, "expires_at": None}
    b64 = base64.b64encode(json.dumps(data).encode()).decode()
    mgr = LicenseManager()
    lic = mgr.load_from_base64(b64)
    assert lic.instance_id == "b64"
    assert mgr.get_license_source() == "env:b64"


def test_validate_without_license():
    mgr = LicenseManager()
    result = mgr.validate()
    assert result.is_valid is False
    assert result.error_message == "No license loaded"


# ---------------------------------------------------------------------------
# Feature / limit gating (lazy validation)
# ---------------------------------------------------------------------------


def _signed_license_data(features=None, limits=None, expires=None):
    priv, pub = generate_keypair()
    data = {
        "version": "1.0",
        "instance_id": "feat-instance",
        "issued_at": int(time.time()) - 100,
        "expires_at": expires,
        "features": features or {},
        "limits": limits or {},
    }
    data["signature"] = sign_license(priv, data)
    return data, pub


@pytest.mark.parametrize(
    ("features", "name", "default", "expected"),
    [
        ({"bond": True}, "bond", False, True),
        ({"bond": False}, "bond", True, False),
        ({"sso": False}, "sso", False, False),
        ({}, "missing", False, False),
        ({}, "missing", True, True),
        ({"tiered": {"max_rules": 50}}, "tiered", False, True),
        ({"tiered": {"max_rules": 50}}, "tiered", False, True),
        (
            {"num_feature": 5},
            "num_feature",
            False,
            False,
        ),  # non-bool non-dict -> default
    ],
)
def test_has_feature_values(features, name, default, expected):
    data, pub = _signed_license_data(features=features)
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    mgr.validate()
    assert mgr.has_feature(name, default) is expected


def test_get_feature_config_tiered():
    data, pub = _signed_license_data(
        features={"advanced_automod": {"max_rules": 50, "ai": True}}
    )
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    mgr.validate()
    assert mgr.get_feature_config("advanced_automod") == {"max_rules": 50, "ai": True}


def test_get_feature_config_missing_returns_default():
    data, pub = _signed_license_data()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    mgr.validate()
    assert mgr.get_feature_config("nope", "fallback") == "fallback"
    assert mgr.get_feature_config("nope") is None


def test_get_feature_limit():
    data, pub = _signed_license_data(limits={"max_users": 1000})
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    mgr.validate()
    assert mgr.get_feature_limit("max_users") == 1000
    assert mgr.get_feature_limit("missing", 42) == 42


def test_has_feature_without_explicit_validate_lazy():
    # Lazy validation: expired license must NOT grant features even if
    # validate() was never called explicitly.
    data, pub = _signed_license_data(
        features={"bond": True}, expires=int(time.time()) - 10
    )
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    assert mgr.has_feature("bond", default=False) is False
    assert mgr.get_feature_config("bond") is None
    assert mgr.get_feature_limit("max_users", 5) == 5
    assert mgr.get_instance_id() is None


def test_get_instance_id_when_valid():
    data, pub = _signed_license_data()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    mgr.validate()
    assert mgr.get_instance_id() == "feat-instance"


def test_to_dict_round_trip():
    data, pub = _signed_license_data(features={"bond": True})
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    d = mgr.to_dict()
    assert d["instance_id"] == "feat-instance"
    assert d["features"]["bond"] is True
    assert d["signature"] == data["signature"]
    assert set(d) == {
        "version",
        "type",
        "instance_id",
        "issued_at",
        "expires_at",
        "features",
        "limits",
        "allowed_hosts",
        "provisional",
        "signature",
    }


def test_to_dict_without_license():
    assert LicenseManager().to_dict() == {}


def test_get_expiry_timestamp():
    data, pub = _signed_license_data()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    assert mgr.get_expiry_timestamp() == data["expires_at"]


def test_is_valid_lazy():
    data, pub = _signed_license_data()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    assert mgr.is_valid() is True


# ---------------------------------------------------------------------------
# Provisional / test licenses, "all" meta-feature, base features
# ---------------------------------------------------------------------------


def _test_license_dict(**overrides):
    base = {
        "version": "1.0",
        "type": "test",
        "instance_id": "plexichat-selftest",
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + 86400,
        "features": {"all": True},
        "limits": {},
        "allowed_hosts": ["127.0.0.1", "localhost", "::1"],
        "signature": None,
    }
    base.update(overrides)
    return base


def test_test_license_valid_without_signature():
    mgr = LicenseManager()
    mgr.load_from_dict(_test_license_dict())
    result = mgr.validate()
    assert result.is_valid is True
    assert result.is_signature_valid is True  # skipped for test type
    assert mgr.is_provisional() is True
    assert mgr.get_license_type() == "test"


def test_test_license_enables_every_feature():
    mgr = LicenseManager()
    mgr.load_from_dict(_test_license_dict())
    assert mgr.has_feature("bond") is True
    assert mgr.has_feature("plexiscribe") is True
    assert mgr.has_feature("nonexistent_feature") is True


def test_all_meta_feature_enables_everything():
    data, pub = _signed_license_data(features={"all": True})
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    assert mgr.is_valid() is True
    assert mgr.has_feature("bond") is True
    assert mgr.has_feature("plexiscribe") is True


def test_base_feature_messaging_always_available():
    mgr = LicenseManager()
    mgr.load_from_dict(_test_license_dict())
    assert mgr.has_feature("messaging") is True


def test_test_license_host_restriction():
    mgr = LicenseManager()
    mgr.load_from_dict(_test_license_dict())
    assert mgr.is_valid_for_host("127.0.0.1") is True
    assert mgr.is_valid_for_host("localhost") is True
    assert mgr.is_valid_for_host("203.0.113.5") is False


def test_unknown_license_type_rejected():
    with pytest.raises(InvalidLicenseError):
        License(
            version="1.0",
            instance_id="i",
            issued_at=1,
            expires_at=None,
            type="bogus",
        )


# ---------------------------------------------------------------------------
# Resource-limit helpers
# ---------------------------------------------------------------------------


def test_feature_limits_reported_from_license():
    data, pub = _signed_license_data(
        features={"bond": True},
        limits={"max_users": 10, "max_servers": 3, "max_file_size_mb": 25},
    )
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    assert mgr.get_feature_limit("max_users") == 10
    assert mgr.get_feature_limit("max_servers") == 3
    assert mgr.get_feature_limit("max_file_size_mb") == 25
    assert mgr.get_feature_limit("max_users_that_dont_exist", -1) == -1


def test_is_limit_enforced_sentinel_semantics():
    from utils.licensing import is_limit_enforced

    assert is_limit_enforced(-1) is False
    assert is_limit_enforced(None) is False
    assert is_limit_enforced(0) is True
    assert is_limit_enforced(50) is True


def test_all_meta_feature_enables_limit_keys_too():
    data, pub = _signed_license_data(features={"all": True})
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(data)
    assert mgr.has_feature("max_users") is True
    assert mgr.has_feature("max_file_size_mb") is True
