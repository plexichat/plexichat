"""Focused tests for self-test service behavior."""

from types import SimpleNamespace

import pytest


@pytest.mark.unit
class TestRateLimitSelfTest:
    """The self-test proves the limiter is live from its accounting headers.

    The service deliberately avoids bursting to a 429 (that would poison the
    shared IP bucket for the rest of the suite). It makes two ordinary 2xx
    requests and requires ``X-RateLimit-Remaining`` to *decrement* between
    them. These tests pin that contract, including the fail-closed paths.
    """

    @staticmethod
    def _run(responses):
        from src.core.selftest.services.ratelimit import RateLimitTester

        outcome = iter(responses)

        class FakeSession:
            def __init__(self):
                self.calls = 0

            def get(self, _target, timeout):
                assert timeout == 3
                self.calls += 1
                return next(outcome)

        session = FakeSession()
        ctx = SimpleNamespace(
            requests_module=SimpleNamespace(Session=lambda: session),
            make_session=lambda: session,
            base_url="http://127.0.0.1:8000",
            results=[],
        )

        RateLimitTester(ctx).test_rate_limits()
        return session, ctx.results[0]

    def test_decrementing_accounting_headers_pass(self):
        """Two 2xx responses with a falling token count prove the limiter live."""
        FakeResponse = self._FakeResponse
        session, result = self._run(
            [
                FakeResponse(200, {"X-RateLimit-Remaining": "5"}),
                FakeResponse(200, {"X-RateLimit-Remaining": "4"}),
            ]
        )

        assert session.calls == 2
        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["status_codes"] == [200, 200]

    def test_equal_headers_fail_the_check(self):
        """A limiter that never spends tokens is reported as not enforcing."""
        FakeResponse = self._FakeResponse
        _, result = self._run(
            [
                FakeResponse(200, {"X-RateLimit-Remaining": "7"}),
                FakeResponse(200, {"X-RateLimit-Remaining": "7"}),
            ]
        )

        assert result["success"] is False
        assert result["status_codes"] == [200, 200]

    def test_missing_header_stops_after_one_request(self):
        """No accounting header means the check cannot proceed; it fails."""
        FakeResponse = self._FakeResponse
        session, result = self._run([FakeResponse(200, {})])

        assert session.calls == 1
        assert result["success"] is False
        assert result["status_codes"] == [200]

    def test_non_200_stops_the_probe(self):
        """A non-2xx first response ends the probe rather than retrying."""
        FakeResponse = self._FakeResponse
        session, result = self._run([FakeResponse(429, {"X-RateLimit-Remaining": "0"})])

        assert session.calls == 1
        assert result["success"] is False
        assert result["status_code"] == 429
        assert result["status_codes"] == [429]

    class _FakeResponse:
        """Minimal requests.Response stand-in (status_code + headers)."""

        def __init__(self, status_code, headers=None):
            self.status_code = status_code
            self.headers = headers or {}


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
