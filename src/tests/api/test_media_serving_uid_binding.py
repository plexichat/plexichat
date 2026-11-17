"""Regression: a duplicated ``uid`` query parameter must not let a signed
attachment URL skip the per-file access re-check.

``UrlSigner.verify_url`` reads the FIRST ``uid`` value (``parse_qs(...)[0]``)
and strips *all* ``uid`` parameters before recomputing the HMAC. Starlette's
``request.query_params.get("uid")`` returns the LAST value. Appending a second
``uid`` therefore kept the signature valid while making the route compare a
different id against the session user, silently skipping the
``check_file_access`` revocation guard. An attacker holding an unexpired
uid-bound URL could ride it after their channel access was revoked.
"""

from urllib.parse import parse_qs

from src.api.media_serving import _signed_uid_from_query


def test_signed_uid_matches_verifier_first_value_semantics():
    """The route must read the same uid the signature verifier validated."""
    query = "fid=42&exp=9999999999999&sig=abc&uid=111&uid=999"
    # The signer strips every uid and reads only the first for the HMAC/uid
    # binding; the route must resolve the identical value.
    assert _signed_uid_from_query(query) == parse_qs(query).get("uid", [None])[0]
    assert _signed_uid_from_query(query) == "111"


def test_signed_uid_respects_parameter_order():
    assert _signed_uid_from_query("uid=999&uid=111") == "999"
    assert _signed_uid_from_query("uid=111&uid=999") == "111"


def test_signed_uid_absent_returns_none():
    assert _signed_uid_from_query("fid=42&exp=1&sig=abc") is None
    assert _signed_uid_from_query("") is None


def test_appended_uid_cannot_change_resolved_value():
    """The exact attack: appending a second uid leaves the resolved uid intact.

    With the old ``request.query_params.get("uid")`` the resolved value became
    ``999`` while the verifier still validated ``111``; the re-check was
    skipped. The helper must stay pinned to the first, verified value.
    """
    original = "fid=42&exp=9999999999999&sig=abc&uid=111"
    tampered = original + "&uid=999"
    assert _signed_uid_from_query(original) == "111"
    assert _signed_uid_from_query(tampered) == "111"
    # Starlette's last-value behaviour is what we are defending against.
    from starlette.datastructures import QueryParams

    assert QueryParams(tampered).get("uid") == "999"
