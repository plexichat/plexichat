"""Dense matrix tests for the licensing core (structure, signing, expiry,
loading paths, feature gating)."""

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
            {"version": "1.0", "instance_id": "i", "issued_at": -1, "expires_at": None},
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
def test_invalid_structure_raises(kwargs, match):
    with pytest.raises(InvalidLicenseError, match=match):
        License(**kwargs)


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    [
        (1, None),
        (1, 100),
        (100, 1000),
        (2**40, None),
    ],
)
def test_valid_structure_accepted(issued_at, expires_at):
    lic = License(
        version="1.0", instance_id="i", issued_at=issued_at, expires_at=expires_at
    )
    assert lic.expires_at == expires_at


# ---------------------------------------------------------------------------
# Signing / verification
# ---------------------------------------------------------------------------


def _data(**overrides):
    base = {
        "version": "1.0",
        "instance_id": "inst",
        "issued_at": int(time.time()) - 100,
        "expires_at": None,
        "features": {},
        "limits": {},
    }
    base.update(overrides)
    return base


def _signed_manager(data, keypair):
    priv, pub = keypair
    signed = {**data, "signature": sign_license(priv, data)}
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(signed)
    return mgr


@pytest.mark.parametrize(
    "features",
    [{}, {"bond": True}, {"tiered": {"max": 10}}, {"a": True, "b": False}],
)
def test_signed_round_trip_valid(features):
    mgr = _signed_manager(_data(features=features), generate_keypair())
    result = mgr.validate()
    assert result.is_valid is True
    assert result.is_signature_valid is True
    assert result.is_expired is False


def test_tampered_feature_invalidates_signature():
    data = _data(features={"bond": False})
    priv, pub = generate_keypair()
    sig = sign_license(priv, data)
    tampered = {**data, "features": {"bond": True}, "signature": sig}
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(tampered)
    assert mgr.validate().is_valid is False
    assert mgr.validate().is_signature_valid is False


def test_tampered_limits_invalidates_signature():
    data = _data(limits={"max_users": 10})
    priv, pub = generate_keypair()
    sig = sign_license(priv, data)
    tampered = {**data, "limits": {"max_users": 99999}, "signature": sig}
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(tampered)
    assert mgr.validate().is_valid is False


def test_wrong_key_invalidates():
    _, pub = generate_keypair()
    priv2, _ = generate_keypair()
    data = _data()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict({**data, "signature": sign_license(priv2, data)})
    assert mgr.validate().is_valid is False


def test_missing_signature_invalidates():
    _, pub = generate_keypair()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(_data())
    result = mgr.validate()
    assert result.is_valid is False
    assert result.error_message is not None


def test_validate_without_license():
    result = LicenseManager().validate()
    assert result.is_valid is False
    assert result.error_message == "No license loaded"


# ---------------------------------------------------------------------------
# Signature coverage
#
# The Ed25519 signature must cover every field the parser accepts and that
# changes what the license grants -- ``type`` and ``allowed_hosts`` included.
# They used to sit outside the signed payload while ``sign_license`` signed
# the whole mapping (so vendor-signed licenses in the documented format were
# rejected outright), and the unsigned-test-license exemption was keyed on
# ``type`` alone, so rewriting ``"type": "test"`` on a signed license turned
# signature verification off and unlocked every premium feature.
# ---------------------------------------------------------------------------


def _documented_license(**overrides):
    """A license in the format documented in the core module docstring."""
    now = int(time.time())
    base = {
        "version": "1.0",
        "type": "standard",
        "instance_id": "inst",
        "issued_at": now - 100,
        "expires_at": now + 100000,
        "features": {"bond": True},
        "limits": {"max_users": 50},
        "allowed_hosts": [],
    }
    base.update(overrides)
    return base


def _signed_documented(**overrides):
    priv, pub = generate_keypair()
    data = _documented_license(**overrides)
    return {**data, "signature": sign_license(priv, data)}, priv, pub


def test_documented_format_signature_round_trips():
    signed, _, pub = _signed_documented()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(signed)
    result = mgr.validate()
    assert result.is_valid is True
    assert result.is_signature_valid is True
    assert result.instance_id == "inst"


def test_unsigned_provisional_license_still_validates():
    """The genuine CI self-test license stays accepted (it is unsigned)."""
    _, pub = generate_keypair()
    test_license = _documented_license(type="test", signature=None)
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(test_license)
    assert mgr.validate().is_valid is True


def test_type_rewrite_cannot_bypass_signature():
    signed, _, pub = _signed_documented()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict({**signed, "type": "test"})
    result = mgr.validate()
    assert result.is_valid is False
    assert result.is_signature_valid is False


def test_type_rewrite_cannot_unlock_premium_features():
    signed, _, pub = _signed_documented()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict({**signed, "type": "test"})
    # has_feature gates on validation first, so an invalid license must not
    # report premium features as enabled even though type == "test".
    assert mgr.has_feature("sso") is False


def test_allowed_hosts_rewrite_invalidates_signature():
    signed, _, pub = _signed_documented()
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict({**signed, "allowed_hosts": ["203.0.113.9"]})
    assert mgr.validate().is_signature_valid is False


def test_signed_test_type_license_is_verified_not_exempted():
    """A license that *presents* a signature is always verified."""
    signed, _, pub = _signed_documented(type="test")
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(signed)
    # signed with the canonical payload that includes type == "test"
    assert mgr.validate().is_signature_valid is True

    # ... and tampering with any covered field still breaks it, because the
    # exemption is not what made it pass.
    mgr2 = LicenseManager(public_key_bytes=pub)
    mgr2.load_from_dict({**signed, "features": {"bond": False}})
    assert mgr2.validate().is_valid is False


def test_signature_key_is_outside_the_signed_payload():
    """Re-signing a mapping that already carries a signature must round-trip."""
    priv, pub = generate_keypair()
    data = _documented_license()
    first = sign_license(priv, data)
    twice = sign_license(priv, {**data, "signature": first})
    assert first == twice


# ---------------------------------------------------------------------------
# Expiry boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delta", "expected_expired", "expected_valid"),
    [
        (1000, False, True),  # future
        (-1, True, False),  # past
        (0, False, True),  # == now: strict >, not expired
        (None, False, True),  # perpetual
    ],
)
def test_expiry_boundaries(monkeypatch, delta, expected_expired, expected_valid):
    # Deterministic clock: validate() must observe a fixed "now" so the
    # expiry==now boundary can never flake across a second boundary.
    fixed_now = 1_700_000_000
    monkeypatch.setattr("utils.licensing.core.time.time", lambda: fixed_now)
    expires_at = None if delta is None else fixed_now + delta
    data = _data(expires_at=expires_at)
    mgr = _signed_manager(data, generate_keypair())
    result = mgr.validate()
    assert result.is_expired is expected_expired
    assert result.is_valid is expected_valid
    assert mgr.is_valid() is expected_valid


def test_expiry_exactly_now_is_not_expired():
    now = int(time.time())
    mgr = _signed_manager(_data(issued_at=now - 5, expires_at=now), generate_keypair())
    assert mgr.validate().is_expired is False


def test_perpetual_license_expiry_timestamp_none():
    mgr = _signed_manager(_data(), generate_keypair())
    assert mgr.get_expiry_timestamp() is None


# ---------------------------------------------------------------------------
# Loading paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", ["%%%", "not base64!", "", "{}---{}"])
def test_invalid_base64_rejected(payload):
    with pytest.raises(InvalidLicenseError):
        LicenseManager().load_from_base64(payload)


def test_base64_round_trip():
    data = _data()
    b64 = base64.b64encode(json.dumps(data).encode()).decode()
    mgr = LicenseManager()
    lic = mgr.load_from_base64(b64)
    assert lic.instance_id == "inst"
    assert mgr.get_license_source() == "env:b64"


def test_file_load_missing_raises(tmp_path):
    with pytest.raises(InvalidLicenseError, match="not found"):
        LicenseManager().load_from_file(str(tmp_path / "missing.json"))


def test_file_load_malformed_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{oops")
    with pytest.raises(InvalidLicenseError):
        LicenseManager().load_from_file(str(p))


def test_file_load_valid(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text(json.dumps(_data()))
    mgr = LicenseManager()
    lic = mgr.load_from_file(str(p))
    assert lic.instance_id == "inst"
    assert mgr.get_license_source() == f"file:{p}"


def test_load_from_dict_source():
    mgr = LicenseManager()
    mgr.load_from_dict(_data())
    assert mgr.get_license_source() == "dict"


# ---------------------------------------------------------------------------
# Feature / limit gating
# ---------------------------------------------------------------------------


def test_has_feature_valid_license():
    mgr = _signed_manager(_data(features={"bond": True}), generate_keypair())
    assert mgr.has_feature("bond") is True
    assert mgr.has_feature("nope", True) is True
    assert mgr.has_feature("nope") is False


def test_tiered_feature_present():
    mgr = _signed_manager(
        _data(features={"advanced": {"rules": 50}}), generate_keypair()
    )
    assert mgr.has_feature("advanced") is True
    assert mgr.get_feature_config("advanced") == {"rules": 50}


def test_expired_license_denies_features():
    data = _data(features={"bond": True}, expires_at=int(time.time()) - 10)
    mgr = _signed_manager(data, generate_keypair())
    assert mgr.has_feature("bond", default=False) is False
    assert mgr.get_feature_config("bond") is None
    assert mgr.get_feature_limit("max_users", 5) == 5
    assert mgr.get_instance_id() is None


def test_limit_values():
    mgr = _signed_manager(_data(limits={"max_users": 250}), generate_keypair())
    assert mgr.get_feature_limit("max_users") == 250
    assert mgr.get_feature_limit("missing", 42) == 42


def test_get_instance_id_when_valid():
    mgr = _signed_manager(_data(instance_id="my-instance"), generate_keypair())
    mgr.validate()
    assert mgr.get_instance_id() == "my-instance"


def test_to_dict_round_trip():
    data = _data(features={"bond": True})
    priv, pub = generate_keypair()
    sig = sign_license(priv, data)
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict({**data, "signature": sig})
    d = mgr.to_dict()
    assert d["signature"] == sig
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


def test_to_dict_without_license_is_empty():
    assert LicenseManager().to_dict() == {}
