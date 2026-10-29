"""The account-purge residue check is opt-in and reaches the CLI.

The check ends by purging an account, so it must never run as part of the
default self-test. It gets its own job (``test:purge_residue``) behind
``--purge-residue``; these tests pin that gate: a default run cannot reach it,
the flag is what turns it on, and the runner refuses to run the check without
it rather than reporting an empty pass.
"""

from __future__ import annotations

import requests
import pytest

from src.cli.main import parse_args
from src.core.selftest.context import SelfTestContext
from src.core.selftest.runner import SelfTestRunner
from src.core.selftest.services.endpoints.tester import EndpointTester


def _context(**kwargs) -> SelfTestContext:
    return SelfTestContext(
        base_url="http://127.0.0.1:8000",
        config={},
        requests_module=requests,
        **kwargs,
    )


def test_residue_check_is_off_unless_asked_for():
    assert _context().purge_residue is False
    assert _context(purge_residue=True).purge_residue is True


def test_default_run_does_not_touch_the_purge_endpoint():
    """Without the flag the check records nothing and makes no request."""
    ctx = _context()
    EndpointTester(ctx).test_purge_residue()

    assert ctx.results == []


def test_runner_refuses_the_check_without_the_flag():
    """A mis-wired call must fail loudly instead of passing an empty suite."""
    runner = SelfTestRunner(base_url="http://127.0.0.1:8000", status_only=True)

    assert runner.purge_residue is False
    with pytest.raises(RuntimeError):
        runner.run_purge_residue()


def test_flag_reaches_the_self_test_command():
    args = parse_args(["self-test", "--purge-residue"])

    assert args.command == "self-test"
    assert args.purge_residue is True


def test_flag_is_off_by_default_on_the_self_test_command():
    args = parse_args(["self-test"])

    assert args.purge_residue is False
