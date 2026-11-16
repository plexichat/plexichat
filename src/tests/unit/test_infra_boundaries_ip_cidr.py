"""Boundary tests for IP allow/deny and CIDR parsing (commit 8)."""

import pytest

from src.api.middleware.ip_validation import (
    MAX_ENTRIES,
    IPRuleError,
    ip_in_rules,
    is_ip_permitted,
    parse_cidr,
    parse_ip_entry,
    parse_rule_list,
)


class TestParseCidr:
    def test_valid_v4(self):
        assert str(parse_cidr("10.0.0.0/8")) == "10.0.0.0/8"

    def test_valid_v6(self):
        assert str(parse_cidr("2001:db8::/32")) == "2001:db8::/32"

    def test_host_bits_rejected(self):
        with pytest.raises(IPRuleError):
            parse_cidr("10.0.0.1/8")

    @pytest.mark.parametrize("bad", ["", "not-an-ip", "10.0.0.0/33", "a\r\nb", 12])
    def test_invalid(self, bad):
        with pytest.raises(IPRuleError):
            parse_cidr(bad)

    def test_too_long(self):
        with pytest.raises(IPRuleError):
            parse_cidr("1" * 200)


class TestParseEntry:
    def test_plain_ipv4(self):
        assert str(parse_ip_entry("192.168.1.10")) == "192.168.1.10/32"

    def test_plain_ipv6(self):
        assert str(parse_ip_entry("::1")) == "::1/128"

    def test_cidr(self):
        assert str(parse_ip_entry("10.0.0.0/8")) == "10.0.0.0/8"


class TestRuleList:
    def test_bounded(self):
        entries = [f"10.0.0.{i}/32" for i in range(256)]
        with pytest.raises(IPRuleError):
            parse_rule_list(entries, max_entries=10)
        assert len(parse_rule_list(entries[:10], max_entries=10)) == 10

    def test_default_cap(self):
        with pytest.raises(IPRuleError):
            parse_rule_list(f"10.0.0.{i}.x" for i in range(MAX_ENTRIES + 1))


class TestDecision:
    def test_membership(self):
        rules = parse_rule_list(["10.0.0.0/8", "192.168.1.10"])
        assert ip_in_rules("10.1.2.3", rules)
        assert ip_in_rules("192.168.1.10", rules)
        assert not ip_in_rules("8.8.8.8", rules)

    def test_invalid_client_ip(self):
        assert not ip_in_rules("not-an-ip", [])

    def test_deny_wins(self):
        allow = parse_rule_list(["10.0.0.0/8"])
        deny = parse_rule_list(["10.0.0.5"])
        assert is_ip_permitted("10.0.0.5", allow, deny) is False
        assert is_ip_permitted("10.0.0.6", allow, deny) is True
        assert is_ip_permitted("8.8.8.8", allow, deny) is True

    def test_default_deny(self):
        assert is_ip_permitted("8.8.8.8", [], [], default_allow=False) is False
