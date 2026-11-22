"""Regression: the external-link rule must not recompile a constant regex.

``ExternalLinkRule._extract_host`` runs once per whitespace-delimited token of
every message automod scans. It used to call ``re.compile`` on the same literal
IPv4 pattern on every call (twice in the source, on mutually exclusive paths).
Regex compilation is comparatively expensive and the pattern is static, so it
belongs in a module-level precomputed constant.

The test counts ``re.compile`` calls with that exact source string, which is
deterministic (unlike a wall-clock threshold) and fails on the unfixed code.
"""

import re

from src.core.automod.models import Rule, RuleType
from src.core.automod.rules import links

_IPV4_SRC = r"^\d{1,3}(?:\.\d{1,3}){3}$"


def _rule():
    return Rule(
        id=1,
        server_id=1,
        name="link rule",
        rule_type=RuleType.EXTERNAL_LINKS,
        enabled=True,
        config={"mode": "blacklist", "blacklist": []},
        actions=[],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=1,
        created_at=0,
        updated_at=0,
        created_by=1,
    )


def test_extract_host_does_not_recompile_ipv4_regex(monkeypatch):
    rule = links.ExternalLinkRule(_rule())

    real_compile = re.compile
    compiled = []

    def counting_compile(pattern, *args, **kwargs):
        if isinstance(pattern, str) and pattern == _IPV4_SRC:
            compiled.append(pattern)
        return real_compile(pattern, *args, **kwargs)

    monkeypatch.setattr(links.re, "compile", counting_compile)

    # Tokens that reach each of the two former re.compile sites, plus a plain
    # domain that never does. Repeat to make per-call recompilation obvious.
    tokens = [
        "http://localhost",
        "http://1.2.3.4",
        "1.2.3.4",
        "http://foo.1",
        "example.com",
    ]
    for _ in range(50):
        for token in tokens:
            rule._extract_host(token)

    assert compiled == [], (
        f"IPv4 regex recompiled {len(compiled)} times in _extract_host; "
        "it must be a module-level precomputed constant"
    )


def test_extract_host_behavior_preserved():
    """The precomputed pattern must keep the exact accept/reject semantics."""
    rule = links.ExternalLinkRule(_rule())

    assert rule._extract_host("http://1.2.3.4") == "1.2.3.4"
    assert rule._extract_host("1.2.3.4") == "1.2.3.4"
    assert rule._extract_host("http://localhost") is None
    assert rule._extract_host("http://foo.1") is None
    assert rule._extract_host("example.com") == "example.com"
