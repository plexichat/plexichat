"""A newly loaded license must not inherit a stale validity verdict.

``LicenseManager.has_feature`` (and the config/limit getters) validate lazily:
``_ensure_validated()`` only re-runs validation when ``_validation_result`` is
``None`` or the cached verdict says "valid" but the license has since expired.
That shortcut is only safe while the cached verdict describes the *currently
loaded* license.

Before the fix, ``load_from_dict``/``load_from_base64``/``load_from_file``
replaced ``self._license`` but left ``self._validation_result`` pointing at the
verdict for the previously loaded license. Loading a forged license (tampered
``features``, bad signature, no expiry so time-based revalidation never fires)
right after a valid one made every entitlement getter answer from the forged
license's data under the OLD "valid" verdict -- a full premium-feature bypass
with no signature check.

These tests prove the gate now fails closed for the unvalidated license and
that the legitimate lazy-validation path still grants a real feature.
"""

import base64
import json
import time

import pytest

from utils.licensing.core import LicenseManager, generate_keypair, sign_license

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _signed_license(priv: bytes, *, features: dict) -> dict:
    data = {
        "version": "1.0",
        "instance_id": "instance-1",
        "issued_at": int(time.time()) - 100,
        # Perpetual: no expiry means time-based revalidation can never rescue
        # a stale verdict, which is exactly the dangerous case.
        "expires_at": None,
        "features": features,
        "limits": {},
    }
    data["signature"] = sign_license(priv, data)
    return data


def _forged_license(*, features: dict) -> dict:
    """A standard-type license with a garbage signature and forged features."""
    return {
        "version": "1.0",
        "instance_id": "attacker",
        "issued_at": int(time.time()) - 100,
        "expires_at": None,
        "features": features,
        "limits": {},
        # 64 bytes of base64 -- structurally present, cryptographically bogus.
        "signature": "A" * 88,
    }


def _manager_with_valid_verdict(priv: bytes, pub: bytes) -> LicenseManager:
    """Load a valid license that does NOT grant premium and cache its verdict."""
    mgr = LicenseManager(public_key_bytes=pub)
    mgr.load_from_dict(_signed_license(priv, features={"bond": True}))
    assert mgr.validate().is_valid is True
    assert mgr.has_feature("premium") is False
    return mgr


def test_forged_license_cannot_grant_premium_under_stale_verdict():
    """The fail-open: an invalid hot-swapped license granted a premium feature."""
    priv, pub = generate_keypair()
    mgr = _manager_with_valid_verdict(priv, pub)

    # Hot-swap in an unvalidated, forged license that grants premium. There is
    # deliberately no explicit validate() call: the entitlement getter must
    # validate the new license itself before answering.
    mgr.load_from_dict(_forged_license(features={"premium": True}))

    # FAIL OPEN pre-fix: returned True because the old "valid" verdict was
    # still cached and the forged license has no expiry to trigger a recheck.
    assert mgr.has_feature("premium") is False
    assert mgr.get_feature_config("premium") is None

    # Prove the test is non-vacuous: the freshly loaded license is genuinely
    # invalid, so denying the feature is the only correct verdict.
    assert mgr.validate().is_valid is False


def test_loading_license_clears_cached_verdict():
    """Root-cause invariant: every loader drops the previous license's verdict."""
    priv, pub = generate_keypair()
    mgr = _manager_with_valid_verdict(priv, pub)

    mgr.load_from_dict(_forged_license(features={"premium": True}))
    assert mgr._validation_result is None

    # base64 loader funnels through the same parser and must behave identically.
    mgr2 = _manager_with_valid_verdict(priv, pub)
    forged_b64 = base64.b64encode(
        json.dumps(_forged_license(features={"premium": True})).encode("utf-8")
    ).decode("ascii")
    mgr2.load_from_base64(forged_b64)
    assert mgr2._validation_result is None
    assert mgr2.has_feature("premium") is False


def test_valid_license_still_grants_its_features_after_load():
    """The fix must not break lazy validation of a genuinely valid license."""
    priv, pub = generate_keypair()
    mgr = LicenseManager(public_key_bytes=pub)
    # Deliberately do NOT call validate(): has_feature must validate on demand.
    mgr.load_from_dict(_signed_license(priv, features={"bond": True}))
    assert mgr.has_feature("bond") is True
    assert mgr.has_feature("premium") is False
