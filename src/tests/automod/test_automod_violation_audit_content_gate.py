"""Regression: the automod violation/audit-log read endpoints must not hand
the private offending message body to a moderator who cannot view the channel
the violation occurred in.

N711 gated the per-recipient ``alert_moderators`` notification and N749 gated
the channel alert, but the audit trail those alerts point at is a THIRD
content-bearing destination: ``automod_violations.matched_content`` (and the
content-bearing ``metadata.examples``) is returned verbatim by
``get_server_automod_violations`` / ``get_server_automod_violation``. The only
gate on those routes is the server-wide ``server.automod`` bit, which a member
can hold while a channel override denies ``channels.view`` / ``messages.read``
on a specific private channel -- exactly the audience N711 decided must not
receive the body.

The fix gates every returned violation on the reader's view/read of that
violation's own channel, redacting the body (and content-bearing metadata) when
the check cannot be established. Fail closed: an unwired store, a missing id
or a raising permission lookup must redact too.
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from src.api.routes.servers import automod as automod_routes
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType, TokenInfo
from src.core.automod.models import RuleType, ViolationSeverity

pytestmark = [pytest.mark.automod, pytest.mark.security]

SERVER = 771
PUBLIC_CHANNEL = 882
PRIVATE_CHANNEL = 883
MODERATOR = 9101
PRIVATE_BODY = "PRIVATE-AUDIT-LOG-BODY-MARKER"
PUBLIC_BODY = "PUBLIC-AUDIT-BODY-MARKER"


def _token():
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=SnowflakeID(str(MODERATOR)),
        user_id=SnowflakeID(str(MODERATOR)),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="mod",
        account_type=AccountType.USER,
    )


def _violation(vid, channel_id, matched_content, *, metadata=None):
    return SimpleNamespace(
        id=vid,
        server_id=SERVER,
        channel_id=channel_id,
        user_id=42,
        rule_id=1,
        rule_type=RuleType.KEYWORD,
        matched_content=matched_content,
        severity=ViolationSeverity.MEDIUM,
        actions_taken=[],
        created_at=0,
        metadata=metadata if metadata is not None else {},
    )


class _Servers:
    """Moderator holds server.automod but is denied view on PRIVATE_CHANNEL."""

    def __init__(self, *, raises=False, allow_all=False):
        self.raises = raises
        self.allow_all = allow_all

    def require_permission(self, user_id, server_id, permission, channel_id=None):
        if permission != "server.automod":
            raise AssertionError(f"unexpected permission gate: {permission}")
        return None

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        if self.raises:
            raise RuntimeError("permission store unavailable")
        if self.allow_all:
            return True
        return int(channel_id) == PUBLIC_CHANNEL


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _set_servers(monkeypatch, servers):
    # Patch the route module's own `api` handle: an earlier automod test may
    # have installed a minimal `src.api` stub in sys.modules, so patching the
    # package directly is not reliable across the whole suite.
    monkeypatch.setattr(
        automod_routes.api, "get_servers", lambda: servers, raising=False
    )


def _run_list(monkeypatch, servers, violations):
    _set_servers(monkeypatch, servers)
    monkeypatch.setattr(automod_routes, "_parse_id", lambda value, label: int(value))
    from src.core import automod

    monkeypatch.setattr(automod, "get_violations", lambda *a, **kw: violations)
    return _call(
        automod_routes.get_server_automod_violations,
        server_id=str(SERVER),
        current_user=_token(),
    )


def _run_single(monkeypatch, servers, violation):
    _set_servers(monkeypatch, servers)
    monkeypatch.setattr(automod_routes, "_parse_id", lambda value, label: int(value))
    from src.core import automod

    monkeypatch.setattr(automod, "get_violation", lambda *a, **kw: violation)
    return _call(
        automod_routes.get_server_automod_violation,
        server_id=str(SERVER),
        violation_id="2",
        current_user=_token(),
    )


@pytest.mark.automod
def test_denied_view_moderator_gets_no_private_body_in_audit_log(monkeypatch):
    """The decisive leak: the audit trail exposes a body the WS alert hides."""
    violations = [
        _violation(1, PUBLIC_CHANNEL, PUBLIC_BODY),
        _violation(
            2,
            PRIVATE_CHANNEL,
            PRIVATE_BODY,
            metadata={"examples": [PRIVATE_BODY]},
        ),
    ]
    result = _run_list(monkeypatch, _Servers(), violations)
    bodies = {int(v.id): v.matched_content for v in result}

    assert PUBLIC_BODY in bodies[1], "control: viewable channel keeps its body"
    assert PRIVATE_BODY not in bodies[2], (
        "a moderator denied view on the private channel received the offending "
        "message body through the automod violation/audit-log API"
    )
    assert PRIVATE_BODY not in str(result[1].metadata), (
        "content-bearing metadata must be redacted alongside matched_content"
    )


@pytest.mark.automod
def test_single_violation_endpoint_gates_private_body(monkeypatch):
    """The single-violation audit read must apply the same fail-closed gate."""
    violation = _violation(
        2, PRIVATE_CHANNEL, PRIVATE_BODY, metadata={"examples": [PRIVATE_BODY]}
    )
    result = _run_single(monkeypatch, _Servers(), violation)
    assert PRIVATE_BODY not in result.matched_content
    assert PRIVATE_BODY not in str(result.metadata)


@pytest.mark.automod
def test_unwired_or_raising_permission_store_strips_content(monkeypatch):
    """Fail closed: no authoritative view check means no private body."""
    violations = [_violation(2, PRIVATE_CHANNEL, PRIVATE_BODY)]
    for servers in (_Servers(raises=True), _Servers(allow_all=False)):
        result = _run_list(monkeypatch, servers, violations)
        assert PRIVATE_BODY not in result[0].matched_content
