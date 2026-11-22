"""Force-password-change target id must use the canonical snowflake gate.

``/admin/auth/force-password-change/{target_admin_id}`` used to coerce its
caller-controlled path id with a bare ``int()``.  Python's ``int`` accepts
Unicode decimal homoglyphs (``"١٢٣"``, ``"１２３"``), PEP 515 separators
(``"1_2"`` -> 12), signs and values past the 64-bit snowflake ceiling.  The id
that reached the ``force_password_change`` lookup could therefore differ from
the one an upstream ASCII-only validator (proxy, WAF, log/audit pipeline) read
from the very same bytes -- a numeric/id boundary parser differential.

Every other id boundary funnels through ``src.core.security.hardening.parse_id``.
These tests pin the route to that gate and prove the previously-accepted tokens
are now rejected before any mutation is attempted.
"""

import pytest
from fastapi import HTTPException

from src.api.routes.admin import auth as admin_auth

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _ForcePasswordCalls:
    def __init__(self):
        self.targets = []

    def __call__(self, *, acting_admin_id, target_admin_id, client_ip):
        self.targets.append(target_admin_id)
        return True, "done"


@pytest.fixture
def patched_route(monkeypatch):
    """Stub the auth gates and mutation so the handler can be called directly."""
    calls = _ForcePasswordCalls()
    monkeypatch.setattr(admin_auth, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(
        admin_auth, "require_admin_permission", lambda request, permission: 7
    )
    monkeypatch.setattr("src.core.admin.force_password_change", calls, raising=False)
    monkeypatch.setattr("src.utils.net.get_client_ip", lambda request: "127.0.0.1")
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "١٢٣",  # Arabic-Indic digits -> int() gave 123
        "１２３",  # fullwidth digits -> int() gave 123
        "1_2",  # PEP 515 separator -> int() gave 12
        "-1",  # negative
        "0",  # not a valid resource id
        str(2**63),  # past the snowflake ceiling
        "12abc",  # trailing garbage
    ],
)
async def test_non_canonical_target_id_is_rejected_without_mutation(patched_route, raw):
    with pytest.raises(HTTPException) as exc:
        await admin_auth.admin_force_password_change(object(), raw)
    assert exc.value.status_code == 400
    assert patched_route.targets == []


@pytest.mark.asyncio
async def test_canonical_target_id_is_parsed_and_passed_through(patched_route):
    await admin_auth.admin_force_password_change(object(), "123")
    assert patched_route.targets == [123]
