"""Regression tests for the automod evaluation role-lookup hot path.

``check_message`` used to resolve the message author's role ids once in
``_is_exempt`` and then again inside ``_is_exempt_from_rule`` for *every* rule.
For a server with M enabled rules that was M+1 identical ``JOIN`` reads per
message, all returning exactly the same rows.

The role set is now resolved once per message and threaded through every
exemption decision. These tests pin that invariant by counting the query on a
DB double (deterministic, not a flaky wall-clock threshold), and verify that
exemption semantics are unchanged -- including the legacy direct-call path and
the guarantee that role data never leaks between users/messages.
"""

import json

from src.core.automod.manager.evaluation import EvaluationMixin
from src.core.automod.manager.exemptions import ExemptionMixin
from src.core.automod.models import Rule, RuleMatch, RuleType

SERVER, CHANNEL, USER = 1, 2, 3


def _rule(rule_id, exempt_roles=(), applied_roles=(), exempt_channels=()):
    return Rule(
        id=rule_id,
        server_id=SERVER,
        name=f"rule-{rule_id}",
        rule_type=RuleType.KEYWORD,
        enabled=True,
        config={"keywords": ["badword"]},
        actions=[],
        applied_roles=list(applied_roles),
        exempt_roles=list(exempt_roles),
        exempt_channels=list(exempt_channels),
        priority=rule_id,
        created_at=0,
        updated_at=1,
        created_by=1,
    )


class _NoMatchRule:
    def __init__(self, rule):
        self.rule = rule

    def check(self, content, user_id, channel_id, context=None):
        return RuleMatch(
            rule_id=self.rule.id, rule_type=self.rule.rule_type, matched=False
        )


class FakeDB:
    """DB double that counts the role-set query and serves exemption rows."""

    def __init__(
        self,
        *,
        owner_id=None,
        roles_by_user=None,
        admin_roles=(),
        global_channel_exempt=False,
        global_role_exempt=False,
        rule_channel_exempt=False,
        rule_role_exempt=False,
    ):
        self.owner_id = owner_id
        self.roles_by_user = roles_by_user or {USER: list(admin_roles)}
        self.admin_roles = set(admin_roles)
        self.global_channel_exempt = global_channel_exempt
        self.global_role_exempt = global_role_exempt
        self.rule_channel_exempt = rule_channel_exempt
        self.rule_role_exempt = rule_role_exempt
        self.role_queries = 0

    def fetch_one(self, sql, params=()):
        if "srv_servers" in sql:
            if self.owner_id is None:
                return None
            return {"owner_id": self.owner_id}
        if "automod_exemptions" in sql:
            is_global = "rule_id IS NULL" in sql
            is_channel = "target_type = 'channel'" in sql
            if is_global:
                hit = (
                    self.global_channel_exempt
                    if is_channel
                    else self.global_role_exempt
                )
            else:
                hit = self.rule_channel_exempt if is_channel else self.rule_role_exempt
            return {"id": 1} if hit else None
        return None

    def fetch_all(self, sql, params=()):
        if "srv_member_roles" in sql and "srv_members" in sql:
            self.role_queries += 1
            user_id = params[1]
            return [{"role_id": rid} for rid in self.roles_by_user.get(user_id, ())]
        if "srv_roles" in sql:
            return [
                {
                    "permissions": json.dumps(
                        {"administrator": True} if rid in self.admin_roles else {}
                    )
                }
                for rid in params
            ]
        return []


class Manager(EvaluationMixin, ExemptionMixin):
    RULE_CLASSES = {RuleType.KEYWORD: _NoMatchRule}

    def __init__(self, db, rules):
        self._db = db
        self._config = {"enabled": True}
        self._rules = rules

    def _get_server_rules(self, server_id, enabled_only=False):
        return list(self._rules)


def check(db, rules, user=USER, content="a clean message"):
    return Manager(db, rules).check_message(SERVER, CHANNEL, user, content)


# --------------------------------------------------------------------------- #
# Query-count invariant                                                         #
# --------------------------------------------------------------------------- #


def test_role_lookup_once_per_message_independent_of_rule_count():
    """The core regression: M rules cost one role read, not M+1."""
    for n_rules in (1, 8, 32):
        db = FakeDB()
        result = check(db, [_rule(i) for i in range(n_rules)])
        assert result.passed is True
        assert db.role_queries == 1, (
            f"{n_rules} rules caused {db.role_queries} role lookups, expected 1"
        )


def test_repeated_messages_each_pay_exactly_one_role_lookup():
    """No cross-message cache: every check does one scoped read."""
    db = FakeDB()
    mgr = Manager(db, [_rule(i) for i in range(6)])
    for _ in range(20):
        mgr.check_message(SERVER, CHANNEL, USER, "clean")
    assert db.role_queries == 20


def test_role_data_never_leaks_between_users():
    """Each user's role set is resolved from that user only."""
    db = FakeDB(roles_by_user={11: [100], 22: [200]})
    mgr = Manager(db, [_rule(1, exempt_roles=[100])])
    # user 11 holds exempt role 100 -> skipped; user 22 does not -> evaluated.
    # Both paths still perform exactly one scoped read for their own user.
    mgr.check_message(SERVER, CHANNEL, 11, "clean")
    mgr.check_message(SERVER, CHANNEL, 22, "clean")
    assert db.role_queries == 2


# --------------------------------------------------------------------------- #
# Exemption semantics unchanged                                                #
# --------------------------------------------------------------------------- #


def test_owner_still_exempt():
    db = FakeDB(owner_id=USER)
    assert check(db, [_rule(1)]).passed is True
    assert db.role_queries == 1


def test_global_channel_exemption_still_exempt():
    db = FakeDB(global_channel_exempt=True)
    assert check(db, [_rule(1)]).passed is True
    assert db.role_queries == 1


def test_admin_role_still_exempt():
    db = FakeDB(admin_roles=[555], roles_by_user={USER: [555]})
    assert check(db, [_rule(1)]).passed is True
    assert db.role_queries == 1


def test_global_role_exemption_still_exempt():
    db = FakeDB(global_role_exempt=True, roles_by_user={USER: [555]})
    assert check(db, [_rule(1)]).passed is True
    assert db.role_queries == 1


def test_rule_role_exemption_does_not_leak_across_rules():
    """A rule's exempt_roles is evaluated against the shared role set."""
    db = FakeDB(roles_by_user={USER: [100]})
    mgr = Manager(db, [_rule(1, exempt_roles=[100])])
    assert (
        mgr._is_exempt_from_rule(_rule(2, exempt_roles=[100]), USER, CHANNEL, [100])
        is True
    )
    assert (
        mgr._is_exempt_from_rule(_rule(3, exempt_roles=[999]), USER, CHANNEL, [100])
        is False
    )
    # The passed-in role set is reused; no extra DB reads.
    assert db.role_queries == 0


# --------------------------------------------------------------------------- #
# Legacy direct-call fallback                                                  #
# --------------------------------------------------------------------------- #


def test_direct_calls_without_role_ids_still_fetch_fresh():
    db = FakeDB(roles_by_user={USER: [100]})
    mgr = Manager(db, [])
    assert mgr._is_exempt(SERVER, USER, CHANNEL) is False
    assert db.role_queries == 1
    assert mgr._is_exempt_from_rule(_rule(1), USER, CHANNEL) is False
    assert db.role_queries == 2
