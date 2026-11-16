"""Regression: every privileged action leaves an audit entry.

The privileged surface is enumerated explicitly so adding a new sensitive
action without wiring up audit logging makes this test fail. Writes go through
``record_privileged_action``, which refuses unknown actions rather than
silently skipping the audit trail.
"""

import pytest

from src.core.security.hardening import HardeningViolation
from src.core.telemetry.audit import (
    PRIVILEGED_ACTIONS,
    AuditChain,
    missing_privileged_actions,
    record_privileged_action,
)


def _chain(tmp_path):
    return AuditChain(
        str(tmp_path / "audit.jsonl"),
        head_path=str(tmp_path / "audit.jsonl.head"),
        durable=False,
    )


def test_registry_is_nonempty_and_well_formed():
    assert len(PRIVILEGED_ACTIONS) >= 15
    for action in PRIVILEGED_ACTIONS:
        assert isinstance(action, str)
        assert 0 < len(action) <= 128
        assert all(ch.isprintable() for ch in action)


def test_every_privileged_action_is_audited(tmp_path):
    chain = _chain(tmp_path)

    for action in sorted(PRIVILEGED_ACTIONS):
        result = record_privileged_action(
            chain, action, 42, target_type="user", target_id=7
        )
        assert result["seq"] > 0

    assert missing_privileged_actions(chain) == frozenset()
    rows = chain.read_all()
    assert {row["entry"]["action"] for row in rows} == set(PRIVILEGED_ACTIONS)

    valid, count, error = chain.verify_chain()
    assert error is None
    assert valid is True
    assert count == len(PRIVILEGED_ACTIONS)


def test_partial_coverage_is_reported(tmp_path):
    chain = _chain(tmp_path)
    record_privileged_action(chain, "user.ban", 42)

    missing = missing_privileged_actions(chain)
    assert "user.ban" not in missing
    assert "config.update" in missing
    assert "token.revoke" in missing


def test_unknown_action_fails_closed(tmp_path):
    chain = _chain(tmp_path)
    with pytest.raises(HardeningViolation):
        record_privileged_action(chain, "not.a.registered.action", 42)
    assert chain.read_all() == []
