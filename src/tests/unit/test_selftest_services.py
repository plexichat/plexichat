"""Focused tests for self-test service behavior."""

from types import SimpleNamespace

import pytest


@pytest.mark.unit
class TestRateLimitSelfTest:
    """The self-test must not assume a fixed deployment rate limit."""

    def test_burst_uses_effective_limit_header(self):
        """The request count follows the advertised limit and stops at 429."""
        from src.core.selftest.services.ratelimit import RateLimitTester

        class FakeResponse:
            def __init__(self, status_code, headers=None):
                self.status_code = status_code
                self.headers = headers or {}

        responses = iter(
            [
                FakeResponse(200, {"X-RateLimit-Limit": "3"}),
                FakeResponse(200),
                FakeResponse(429),
            ]
        )

        class FakeSession:
            def __init__(self):
                self.calls = 0

            def get(self, _target, timeout):
                assert timeout == 3
                self.calls += 1
                return next(responses)

        session = FakeSession()
        ctx = SimpleNamespace(
            requests_module=SimpleNamespace(Session=lambda: session),
            make_session=lambda: session,
            base_url="http://127.0.0.1:8000",
            results=[],
        )

        RateLimitTester(ctx).test_rate_limits()

        assert session.calls == 3
        assert ctx.results[0]["warning"] is False
        assert ctx.results[0]["status_code"] == 429
        assert ctx.results[0]["status_codes"] == [200, 200, 429]


@pytest.mark.unit
class TestTransferOwnershipBody:
    """POST /servers/{id}/transfer must target a real member.

    Regression: schema-driven generation invented a random snowflake for
    ``new_owner_id``; the core correctly raises MemberNotFoundError for
    non-members (mapped to 404 "Server or member not found"), so the
    auto-loop probe failed deterministically with 404. The generator must
    point at other_user (a seeded member), never at a random id.
    """

    _TRANSFER_BODY = {
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"new_owner_id": {"type": "string"}},
                    "required": ["new_owner_id"],
                }
            }
        }
    }
    _PATH = "/api/v1/servers/123/transfer"

    def _ctx(self, other_id, admin_id):
        return SimpleNamespace(
            _test_password="selftest-pw",
            config={},
            test_other_user_id=other_id,
            test_user_id=admin_id,
            test_server_id=123,
            test_channel_id=456,
            test_conversation_id=None,
            test_message_id=None,
        )

    def _body(self, ctx):
        from src.core.selftest.services.data import DataGenerator

        import copy

        return DataGenerator(ctx).get_minimal_body(
            copy.deepcopy(self._TRANSFER_BODY), self._PATH, "POST"
        )

    def test_transfer_targets_other_member(self):
        body = self._body(self._ctx(other_id=222, admin_id=111))
        assert body["new_owner_id"] == "222"

    def test_transfer_body_is_deterministic_member_not_random(self):
        ctx = self._ctx(other_id=222, admin_id=111)
        first = self._body(ctx)["new_owner_id"]
        second = self._body(ctx)["new_owner_id"]
        assert first == second == "222"

    def test_transfer_falls_back_to_owner_without_other(self):
        body = self._body(self._ctx(other_id=None, admin_id=111))
        assert body["new_owner_id"] == "111"
