"""Regression tests for the W3-C automod fixes.

Covers: regex fallback ReDoS vetting (B1), invite-link allowlist/warn-only
semantics (A2), mention total counting @everyone/@here (A4), repeated-chars
occurrence semantics (A5), scheme-independent external link matching (A6,
including a bare ``host:port``), fail-closed policy helpers (C1) and alert
channel resolution (E1).
"""

import re
from types import SimpleNamespace

from src.core.automod.models import (
    Rule,
    RuleAction,
    ActionType,
    RuleType,
    Violation,
    ViolationSeverity,
)
from src.core.automod.rules.regex import (
    MAX_PATTERN_LENGTH,
    RegexRule,
    _has_redos_risk,
    _pattern_requires_fallback,
)
from src.core.automod.rules.links import ExternalLinkRule, InviteLinkRule
from src.core.automod.rules.mentions import MentionSpamRule


def _rule(rule_type, config):
    return Rule(
        id=1,
        server_id=1,
        name="t",
        rule_type=rule_type,
        enabled=True,
        config=config,
        actions=[RuleAction(action_type=ActionType.DELETE_MESSAGE)],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=0,
    )


class TestRegexRedoSVetting:
    def test_static_analysis_flags_nested_quantifiers(self):
        assert _has_redos_risk(r"(a+)+$")
        assert _has_redos_risk(r"(a*)*")
        assert _has_redos_risk(r"(a|aa)*")
        assert _has_redos_risk(r"(a+b)+c")

    def test_static_analysis_accepts_safe_patterns(self):
        assert not _has_redos_risk(r"\b\d{16}\b")
        assert not _has_redos_risk(r"free\s+money")
        assert not _has_redos_risk(r"[a-z]+(\d)?x*end")

    def test_bounded_group_repetition_accepted(self):
        """Bounded {n}/{n,m} over bounded atoms is NOT a ReDoS shape."""
        # IPv4 octet shape was previously misclassified as unbounded.
        assert not _has_redos_risk(r"(\d{1,3}\.){3}\d{1,3}")
        assert not _has_redos_risk(r"(a|b){2}")
        assert not _has_redos_risk(r"(ab{2,4}c){3}")

    def test_unbounded_nested_quantifiers_still_rejected(self):
        assert _has_redos_risk(r"(a+)+")
        assert _has_redos_risk(r"(a+)*")
        assert _has_redos_risk(r"(\d{1,3})*")
        assert _has_redos_risk(r"((a{2})+)+")

    def test_validate_config_accepts_ipv4_pattern(self):
        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": r"(\d{1,3}\.){3}\d{1,3}"}]}
        )
        assert ok, issues

    def test_regex_rule_compiles_ipv4_and_alternation_patterns(self):
        rule = RegexRule(
            _rule(
                RuleType.REGEX,
                {
                    "patterns": [
                        {"pattern": r"(\d{1,3}\.){3}\d{1,3}", "name": "ipv4"},
                        {"pattern": r"(a|b){2}", "name": "alt"},
                    ]
                },
            )
        )
        names = {p[2] for p in rule._compiled_patterns}
        assert {"ipv4", "alt"} <= names
        match = rule.check("call 192.168.1.100 now", 1, 1)
        assert match.matched

    def test_fallback_constructs_detected(self):
        assert _pattern_requires_fallback("look(?=ahead)")
        assert _pattern_requires_fallback(r"back\1ref")

    def test_validate_config_rejects_dangerous_fallback_pattern(self):
        # Requires fallback (lookahead) AND contains a nested quantifier.
        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": r"(?=(a+)+)"}]}
        )
        assert not ok
        assert any("rejected" in i for i in issues)

    def test_validate_config_accepts_safe_fallback_pattern(self):
        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": r"look(?=ahead)"}]}
        )
        assert ok, issues

    def test_validate_config_enforces_length_cap_always(self):
        long_pattern = "a" * (MAX_PATTERN_LENGTH + 1)
        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": long_pattern}]}
        )
        assert not ok
        assert any("maximum length" in i for i in issues)


class TestMentionSpamTotal:
    def test_everyone_counts_toward_total(self):
        rule = MentionSpamRule(_rule(RuleType.MENTION_SPAM, {"max_total_mentions": 2}))
        match = rule.check("hi @everyone @here", 1, 1)
        # 2 everyone-mentions exceed max_total_mentions=2? No: 2 > 2 is False;
        # add one more to prove they count.
        assert not match.matched

        match = rule.check("hi @everyone @here @here", 1, 1)
        assert match.matched
        assert match.match_details["total_mentions"] == 3

    def test_block_everyone_stays_separate_policy(self):
        rule = MentionSpamRule(
            _rule(
                RuleType.MENTION_SPAM,
                {"max_total_mentions": 100, "block_everyone": True},
            )
        )
        match = rule.check("@everyone", 1, 1)
        assert match.matched
        assert "@everyone/@here usage blocked" in "; ".join(
            match.match_details["violations"]
        )


class TestRepeatedCharsOccurrences:
    def test_single_long_run_triggers_min_occurrences(self):
        from src.core.automod.rules.repeated import RepeatedCharsRule

        rule = RepeatedCharsRule(
            _rule(
                RuleType.REPEATED_CHARS,
                {"max_repeats": 5, "min_occurrences": 3},
            )
        )
        # One run of 20 'a's -> 20 - 5 = 15 occurrences >= 3.
        match = rule.check("hello " + "a" * 20, 1, 1)
        assert match.matched
        assert match.match_details["occurrence_count"] == 15

    def test_short_run_does_not_trigger(self):
        from src.core.automod.rules.repeated import RepeatedCharsRule

        rule = RepeatedCharsRule(
            _rule(
                RuleType.REPEATED_CHARS,
                {"max_repeats": 5, "min_occurrences": 3},
            )
        )
        match = rule.check("hello " + "a" * 7, 1, 1)
        assert not match.matched


class TestInviteLinkSemantics:
    def _check(self, config, content, context=None):
        return InviteLinkRule(_rule(RuleType.INVITE_LINKS, config)).check(
            content, 1, 1, context or {}
        )

    def test_block_all_false_still_blocks_confirmed_invites(self):
        config = {
            "block_all": False,
            "code_length": 8,
            "allowed_codes": ["allowedx"],
        }
        context = {"known_invite_codes": ["badcode1"]}
        match = self._check(config, "join badcode1 now", context)
        assert match.matched
        assert match.match_details["invite_codes"] == ["badcode1"]

    def test_allowed_codes_allowlisted_in_both_modes(self):
        config = {
            "block_all": True,
            "code_length": 8,
            "allowed_codes": ["allowedx"],
        }
        context = {"known_invite_codes": ["allowedx"]}
        assert not self._check(config, "join allowedx now", context).matched

    def test_warn_only_logs_but_does_not_block(self):
        config = {
            "block_all": True,
            "warn_only": True,
            "code_length": 8,
        }
        context = {"known_invite_codes": ["badcode1"]}
        assert not self._check(config, "join badcode1 now", context).matched

    def test_no_known_codes_degrades_to_confirmed_only(self):
        config = {"block_all": True, "code_length": 8}
        # "password" is 8 chars and would false-positive without the guard.
        assert not self._check(config, "my password is safe", {}).matched

    def test_block_all_does_not_block_generic_words_when_invite_confirmed(self):
        """block_all=True must not flip blocking onto ANY pattern candidate."""
        config = {"block_all": True, "code_length": 8}
        context = {"known_invite_codes": ["badcode1"]}
        match = self._check(config, "meeting password frend42x badcode1", context)
        assert match.matched
        # Only the confirmed invite is enforced on; ordinary words stay clean.
        assert match.match_details["invite_codes"] == ["badcode1"]

    def test_block_all_blocks_invite_shaped_candidates(self):
        config = {"block_all": True, "code_length": 8}
        # Invite-URL-shaped codes are enforced on even when never confirmed.
        for content in (
            "join https://discord.gg/badcodex now",
            "see discord.com/invite/badcodex plz",
            "vanity evil.example.com/invite/badcodex",
        ):
            match = self._check(config, content, {})
            assert match.matched, content
            assert match.match_details["invite_codes"] == ["badcodex"]


class TestExternalLinkSchemeIndependence:
    def _rule(self, **config):
        base = {"mode": "whitelist", "whitelist": ["example.com"]}
        base.update(config)
        return ExternalLinkRule(_rule(RuleType.EXTERNAL_LINKS, base))

    def test_ftp_scheme_blocked_in_whitelist_mode(self):
        match = self._rule().check("see ftp://evil.com/x", 1, 1)
        assert match.matched

    def test_protocol_relative_blocked(self):
        match = self._rule().check("see //evil.com/x", 1, 1)
        assert match.matched

    def test_bare_domain_blocked(self):
        match = self._rule().check("visit evil.com/path", 1, 1)
        assert match.matched

    def test_whitelisted_domain_and_subdomain_allowed(self):
        rule = self._rule()
        assert not rule.check("see https://example.com/a", 1, 1).matched
        assert not rule.check("see https://api.example.com/a", 1, 1).matched

    def test_blacklist_mode_catches_any_scheme(self):
        rule = self._rule(mode="blacklist", whitelist=[], blacklist=["evil.com"])
        for text in (
            "ftp://evil.com",
            "//evil.com",
            "evil.com",
            "https://sub.evil.com",
        ):
            assert rule.check(text, 1, 1).matched, text

    def test_bare_host_with_port_is_still_a_link(self):
        """A bare ``host:port`` is a bare domain, not a scheme to strip.

        The scheme prefix used to swallow ``evil.com:`` whole, leaving ``8080/x``
        as the host, so the token was reported as "not a link at all" and a port
        became a one-token bypass of both modes.
        """
        rule = self._rule(mode="blacklist", whitelist=[], blacklist=["evil.com"])
        for text, expected in (
            ("evil.com:8080/x", ["evil.com"]),
            ("go to evil.com:8443", ["evil.com"]),
            ("sub.evil.com:80/a", ["sub.evil.com"]),
        ):
            match = rule.check(text, 1, 1)
            assert match.matched, text
            assert match.match_details["blocked_domains"] == expected, text

    def test_host_with_port_is_blocked_in_whitelist_mode(self):
        rule = self._rule()  # whitelist ["example.com"]
        match = rule.check("see evil.com:8080/x", 1, 1)
        assert match.matched
        assert match.match_details["blocked_domains"] == ["evil.com"]

    def test_whitelisted_host_with_port_stays_allowed(self):
        rule = self._rule()
        assert not rule.check("see example.com:8080/x", 1, 1).matched

    def test_dotted_scheme_still_strips_as_a_scheme(self):
        """A dotted *app* scheme is not host:port; the host after it is judged."""
        rule = self._rule(mode="blacklist", whitelist=[], blacklist=["x.com"])
        match = rule.check("open my.app://x.com/thing", 1, 1)
        assert match.matched
        assert match.match_details["blocked_domains"] == ["x.com"]


class TestFailClosedPolicy:
    def test_default_is_fail_closed(self):
        from src.core import automod

        assert automod.is_fail_closed() is True

    def test_record_internal_error_is_best_effort(self, automod_manager):
        from src.core import automod

        class _Boom(Exception):
            pass

        # Should never raise even when the manager layer works.
        vid = automod.record_internal_error("test", 1, 1, 1, None, _Boom("boom"))
        assert vid is None or isinstance(vid, int)


class TestAlertChannelResolution:
    def test_prefers_server_system_channel(self, automod_manager, test_server):
        server, _owner = test_server
        automod_manager._db.execute(
            "UPDATE srv_servers SET system_channel_id = ? WHERE id = ?",
            (555, server.id),
        )

        from src.core.automod.actions.alert import AlertModeratorsAction

        action = AlertModeratorsAction(automod_manager._db)
        action._messaging = None
        action._notifications = None
        assert action._get_alert_channel(server.id) == 555

    def test_dead_lookup_path_removed(self):
        import inspect
        from src.core.automod.actions.alert import AlertModeratorsAction

        source = inspect.getsource(AlertModeratorsAction._get_alert_channel)
        # No SELECT against automod_rules hunting for alert_channel_id.
        assert "FROM automod_rules" not in source


class TestAlertModeratorNotifications:
    """ALERT_MODERATORS must call a notification method that actually exists.

    It previously called ``manager._should_notify_user`` (no such method
    anywhere in the repo), so ``_send_notifications`` raised AttributeError,
    ``execute()``'s broad handler swallowed it, no moderator was ever
    notified, and the automod audit record was skipped too.
    """

    class _FakeNotificationManager:
        """Mirrors the real NotificationManager surface the action uses.

        Deliberately does NOT define ``_should_notify_user``: the pre-fix code
        called that name and must fail here.
        """

        def __init__(self):
            self.created = []
            self.unread_updates = []
            self.checked = []

        def get_notification_settings(self, user_id, server_id=None):
            return SimpleNamespace(user_id=user_id, server_id=server_id)

        def get_channel_override(self, user_id, channel_id):
            return None

        def _should_notify_user_optimized(
            self,
            user_id,
            author_id,
            server_id,
            channel_id,
            mention_type,
            settings,
            override,
        ):
            self.checked.append((user_id, mention_type))
            return True

        def _create_notification(self, **kwargs):
            self.created.append(kwargs)
            return SimpleNamespace(id=1)

        def _update_unread_count(self, *args, **kwargs):
            self.unread_updates.append((args, kwargs))

    class _FakeNotifications:
        def __init__(self, manager):
            self._manager = manager

        def _get_manager(self):
            return self._manager

    @staticmethod
    def _violation():
        return Violation(
            id=1,
            server_id=1,
            channel_id=None,
            user_id=42,
            message_id=None,
            rule_id=1,
            rule_type=RuleType.KEYWORD,
            matched_content="bad",
            actions_taken=[ActionType.ALERT_MODERATORS],
            severity=ViolationSeverity.MEDIUM,
            created_at=0,
        )

    def test_notifies_each_moderator(self, automod_manager):
        from src.core.automod.actions.alert import AlertModeratorsAction

        manager = self._FakeNotificationManager()
        action = AlertModeratorsAction(automod_manager._db)
        action._notifications = self._FakeNotifications(manager)

        action._send_notifications([7, 8], self._violation(), None)

        assert [c["user_id"] for c in manager.created] == [7, 8]
        assert len(manager.unread_updates) == 2
        assert [uid for uid, _ in manager.checked] == [7, 8]

    def test_action_does_not_call_a_missing_notification_method(self):
        import inspect
        from src.core.automod.actions.alert import AlertModeratorsAction
        from src.core.notifications.manager.creator import NotificationCreatorMixin

        # The gate the fix targets really exists...
        assert hasattr(NotificationCreatorMixin, "_should_notify_user_optimized")
        # ...and the action must not reach for the non-existent spelling.
        source = inspect.getsource(AlertModeratorsAction._send_notifications)
        assert not re.search(r"\._should_notify_user\b", source)
