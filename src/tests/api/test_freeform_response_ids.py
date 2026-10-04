"""Free-form (property-less) JSON responses must not carry numeric snowflakes.

``test_openapi_snowflake_ids`` allowlists the 2xx responses that declare no
properties at all (``Dict[str, Any]``), because the schema walk cannot see
inside them. This test exercises the reachable ones over HTTP and scans the
actual JSON for any integer at or above 2**53 — the threshold past which a JS
``Number`` cannot hold the value exactly. A response that starts returning a
numeric snowflake fails here even though its schema is opaque.
"""

from typing import Any

# (method, path, needs_auth)
CASES = [
    ("GET", "/api/v1/capabilities/artifacts", True),
    ("GET", "/api/v1/suite/apps", True),
    ("GET", "/api/v1/suite/home/recent", True),
    ("GET", "/api/v1/suite/home/activity", True),
    ("GET", "/api/v1/public/license/info", False),
]

MIN_SNOWFLAKE = 2**53


def _large_ints(node: Any, path: str = "$") -> list[str]:
    """Return ``key path = value`` for every int >= 2**53 in a JSON payload."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and abs(value) >= MIN_SNOWFLAKE:
                found.append(f"{child} = {value}")
            found += _large_ints(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _large_ints(value, f"{path}[{index}]")
    return found


def test_large_int_scanner_flags_numeric_snowflakes():
    assert _large_ints({"id": 9007199254740993}) == ["$.id = 9007199254740993"]
    assert _large_ints({"id": "9007199254740993"}) == []
    assert _large_ints({"nested": [{"user_id": 10**18}]}) == [
        "$.nested[0].user_id = 1000000000000000000"
    ]
    # Small rowids and booleans are not snowflakes.
    assert _large_ints({"id": 42, "ok": True}) == []


def test_freeform_responses_carry_string_ids(test_client, auth_headers):
    offenders: list[str] = []
    for method, path, needs_auth in CASES:
        headers = auth_headers if needs_auth else None
        r = test_client.request(method, path, headers=headers)
        assert r.status_code == 200, f"{method} {path} -> {r.status_code}: {r.text}"
        if "json" not in r.headers.get("content-type", ""):
            continue
        offenders += [f"{method} {path} {hit}" for hit in _large_ints(r.json())]

    assert not offenders, (
        "Free-form JSON responses returned a numeric snowflake id; serialize "
        "it as a decimal string instead:\n" + "\n".join(offenders)
    )
