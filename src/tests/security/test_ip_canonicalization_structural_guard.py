"""Structural guard: every security-relevant IP key is canonicalized first.

Follow-up to the IP-canonicalization campaign (IP blacklist ``2e4d2662``, IPv6
zone stripping ``91ab5154``, and API-token/audit indexes ``8dc80528``). Those
commits routed the mutable-state keys that attackers can spell many ways
(``blind_index``/``fast_blind_index`` only lower/strip) through
:func:`src.core.auth.ip_canonical.canonicalize_ip_address`, or through an
equivalent ``ipaddress.ip_address()`` normalizer in the limiter and the login
failure-budget helper. Without that, one host behind a trusted proxy rotates
X-Forwarded-For spellings to reset failure budgets, evade a ban, or inflate
``COUNT(DISTINCT ip_index)``.

Runtime tests cover each site individually but nothing stops a later edit from
replacing a canonicalized argument with the raw address, or from adding a brand
new raw blind-index IP key under a name nobody thought to test. This is a pure
AST ratchet (no app import/config/DB) with three halves:

1. ``DOCUMENTED_IP_KEY_SITES`` enumerates every production site that derives a
   security-relevant key/index/bucket from an IP (rate-limit principal, login
   failure budget, IP blacklist index + verdict-cache key, API-token abuse and
   last-used index, audit/known-IP indexes, telemetry's rate-limit bucket). Each
   entry's canonicalization/authoritative evidence must remain in the function
   body as parsed from the committed tree.

2. A whole-tree sweep of every production module flags any *new* raw
   ``blind_index``/``fast_blind_index`` IP key. Canonicalized arguments (inline
   or via a canonicalizer-derived alias) are accepted; raw ones are only
   suppressed when they are an exactly documented exception with a reason. A
   novel function/key name with a raw IP argument fails closed. The only
   suppressed sites are session binding (raw is intentional for availability:
   the stored index is minted from the same raw form), the contested 2FA file,
   and immutable telemetry records.

3. The committed-tree reader really returns HEAD blobs, and both detectors are
   proven non-vacuous: the self-tests flag a synthetic raw ``fast_blind_index``
   key, accept canonicalized forms, and the sweep must find exactly the eight
   documented raw sites when suppression is removed.

Blobs are read from COMMITTED HEAD (``git show HEAD:<path>``), not the working
tree: many production files are contested by parallel agents whose stale copies
can mislead a working-tree scan while a whole-file commit could silently revert
a fix.
"""

from __future__ import annotations

import ast
import subprocess
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Git / committed-tree plumbing
# ---------------------------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run and pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _make_blob_reader(commit: str):
    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}")
        assert proc.returncode == 0, f"blob missing from {commit}: {path}"
        return proc.stdout

    return read


_EXCLUDED_COMPONENTS = frozenset({"tests", "selftest"})


def _is_excluded(path: str) -> bool:
    return any(part in _EXCLUDED_COMPONENTS for part in path.split("/")[:-1])


@lru_cache(maxsize=None)
def _production_files(commit: str) -> tuple[str, ...]:
    proc = _git("ls-tree", "-r", "--name-only", commit, "--", "src")
    assert proc.returncode == 0, f"cannot list src: {proc.stderr.strip()}"
    files = tuple(
        p for p in proc.stdout.splitlines() if p.endswith(".py") and not _is_excluded(p)
    )
    assert files, "no production python modules found in the committed tree"
    return files


# ---------------------------------------------------------------------------
# Vocabulary: what counts as "canonicalized" and as a "raw IP blind index"
# ---------------------------------------------------------------------------

# Either the shared helper or an equivalent inline ``ipaddress`` normalization.
# ``_canonical_source_ip`` / ``normalise_ip_address`` / ``normalise_principal``
# are the two reviewed inline normalizers used by the login budget and limiter.
CANON_MARKERS: tuple[str, ...] = (
    "canonicalize_ip_address",
    "ipaddress.ip_address",
    "_canonical_source_ip",
    "normalise_ip_address",
    "normalise_principal",
)

# Blind-index family used to key mutable state (the exact function named in the
# campaign and in the non-vacuity requirement is ``fast_blind_index``).
IP_HASH_FUNCS = frozenset(
    {"blind_index", "fast_blind_index", "legacy_fast_blind_index"}
)

# A blind-index call is "about IP" if its domain label is an IP domain, or one
# of its arguments is rooted at an IP-named variable. The domain label keeps the
# detector failing closed even when the variable is given a novel name.
IP_DOMAIN_LABELS = frozenset({"ip_address", "ip", "client_ip", "source_ip"})
IP_NAME_HINTS = frozenset(
    {
        "ip",
        "ip_address",
        "client_ip",
        "source_ip",
        "peer_ip",
        "remote_ip",
        "src_ip",
        "forwarded_for",
        "xff",
        "ip_addr",
        "ipaddr",
    }
)


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _has_ip_hint(node: ast.AST) -> bool:
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name) and inner.id in IP_NAME_HINTS:
            return True
        if isinstance(inner, ast.Attribute) and inner.attr in IP_NAME_HINTS:
            return True
    return False


def _is_ip_domain_call(call: ast.Call) -> bool:
    labels = any(
        isinstance(arg, ast.Constant)
        and isinstance(arg.value, str)
        and arg.value.strip() in IP_DOMAIN_LABELS
        for arg in call.args
    )
    return labels or any(_has_ip_hint(arg) for arg in call.args)


def _canonicalized_names(fn: ast.AST) -> set[str]:
    """Local names assigned from a canonicalizer (incl. ``... or raw``)."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or node.value is None:
            continue
        if not any(marker in ast.unparse(node.value) for marker in CANON_MARKERS):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


# ---------------------------------------------------------------------------
# Part 1: documented production sites
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IpKeySite:
    """A production IP-key site whose evidence must survive in HEAD.

    ``must_contain`` markers prove the entry still points at the real site.
    ``mode`` is ``"canon"`` (canonicalization must be in the function body) or
    ``"downstream"`` (the IP is handed to the limiter, which canonicalizes via
    ``normalise_ip_address``; telemetry rows are immutable, so no local index).
    """

    label: str
    rel_path: str
    func: str
    must_contain: tuple[str, ...]
    mode: str
    why: str


DOCUMENTED_IP_KEY_SITES: tuple[IpKeySite, ...] = (
    IpKeySite(
        "rate-limit IP principal normalizer",
        "src/core/ratelimit/manager/principals.py",
        "normalise_ip_address",
        ("ipaddress.ip_address", "ipv4_mapped", "split("),
        "canon",
        "Every rate-limit bucket keyed on an unauthenticated client IP funnels "
        "through this normalizer (normalise_principal -> _generate_bucket_key); "
        "the ipaddress parse folds mapped IPv4 and strips IPv6 zone ids so one "
        "host cannot rotate spellings into fresh buckets.",
    ),
    IpKeySite(
        "login failure-budget IP canonicalizer",
        "src/core/auth/managers/session/login.py",
        "_canonical_source_ip",
        ("ipaddress.ip_address", "ipv4_mapped", "split("),
        "canon",
        "Failure rows (per-user and global credential-stuffing budget) must "
        "agree on one source address; the inline ipaddress normalizer folds "
        "mapped IPv4 and zone ids before blind_index sees the value.",
    ),
    IpKeySite(
        "login failure-budget ip_index",
        "src/core/auth/managers/session/login.py",
        "_failure_ip_index",
        ("blind_index", "_canonical_source_ip"),
        "canon",
        "All auth_failed_logins ip_index writes go through here, so the blind "
        "index is taken from _canonical_source_ip's normalized address, never "
        "the raw XFF spelling.",
    ),
    IpKeySite(
        "IP-blacklist verdict cache key",
        "src/core/auth/managers/ip_blacklist.py",
        "_ip_blocked_cache_key",
        ("ip_blocked", "canonicalize_ip_address"),
        "canon",
        "The per-IP verdict cache key must use the canonical address, else a "
        "banned host gets a fresh cache miss per XFF spelling while the row "
        "lookup is canonicalized.",
    ),
    IpKeySite(
        "IP-blacklist block_ip ip_index",
        "src/core/auth/managers/ip_blacklist.py",
        "block_ip",
        ("canonicalize_ip_address", "fast_blind_index"),
        "canon",
        "Admin-supplied block entries and resolver-produced verdicts must "
        "hash to the same ip_index; canonicalize_ip_address is applied first.",
    ),
    IpKeySite(
        "IP-blacklist unblock_ip ip_index",
        "src/core/auth/managers/ip_blacklist.py",
        "unblock_ip",
        ("canonicalize_ip_address", "fast_blind_index"),
        "canon",
        "Unblocking must derive the same canonical ip_index as block_ip.",
    ),
    IpKeySite(
        "IP-blacklist is_ip_blocked ip_index",
        "src/core/auth/managers/ip_blacklist.py",
        "is_ip_blocked",
        ("canonicalize_ip_address", "fast_blind_index"),
        "canon",
        "Verdict lookup derives the blind index from the canonical IP so a "
        "ban cannot be evaded by address spelling.",
    ),
    IpKeySite(
        "API-token abuse event ip_index",
        "src/core/auth/managers/api_token.py",
        "_record_api_access_token_event",
        ("canonicalize_ip_address", "fast_blind_index", "ip_index"),
        "canon",
        "Rows feeding COUNT(DISTINCT ip_index) in the abuse summary must "
        "collapse one host's spellings into a single index.",
    ),
    IpKeySite(
        "API-token verify last_used_ip_index",
        "src/core/auth/managers/api_token.py",
        "verify_api_access_token",
        ("canonicalize_ip_address", "fast_blind_index", "last_used_ip_index"),
        "canon",
        "The mutable last-used index is canonicalized on every verification "
        "(outside the verdict cache) so token-usage telemetry is attributable "
        "to one host.",
    ),
    IpKeySite(
        "audit ip_index",
        "src/core/auth/managers/base.py",
        "_log_audit",
        ("canonicalize_ip_address", "fast_blind_index", "ip_index"),
        "canon",
        "Distinct-IP audit queries must not count one host many times; the "
        "audit ip_index is derived from the canonical address.",
    ),
    IpKeySite(
        "known-IP ip_index",
        "src/core/auth/managers/base.py",
        "_track_ip",
        ("canonicalize_ip_address", "fast_blind_index", "ip_index"),
        "canon",
        "auth_known_ips is UNIQUE(user_id, ip_index); canonicalization keeps "
        "equivalent spellings from creating duplicate known-IP rows.",
    ),
    IpKeySite(
        "telemetry rate-limit bucket (downstream canonicalization)",
        "src/api/routes/telemetry.py",
        "submit_response_times",
        ("get_client_ip", "acheck_rate_limit", "ip_address=client_ip"),
        "downstream",
        "Telemetry rows are append-only/immutable analytics: raw IPs are never "
        "used as a local blind-index key there. The only IP-derived key is the "
        "rate-limit bucket, which the route hands to acheck_rate_limit; the "
        "limiter canonicalizes it via normalise_ip_address (site 1 above).",
    ),
)


@dataclass(frozen=True)
class RawIpException:
    """A raw ``blind_index(ip, ...)`` that is *intentionally* raw."""

    label: str
    rel_path: str
    func: str
    reason: str


# These are the ONLY raw blind-index IP derivations allowed. New raw sites need
# a reviewed entry here with a reason; the sweep rejects everything else.
DOCUMENTED_RAW_EXCEPTIONS: tuple[RawIpException, ...] = (
    RawIpException(
        "session creation binding index",
        "src/core/auth/managers/session/lifecycle.py",
        "_create_session",
        "Availability: the stored auth_sessions.ip_index is minted from the "
        "raw presented address, so the live verifier must hash the same raw "
        "form. Canonicalizing only one side would break token binding for all "
        "already-issued sessions; binding is symmetric by construction.",
    ),
    RawIpException(
        "session verification binding index",
        "src/core/auth/managers/session/lifecycle.py",
        "_verify_session_token",
        "Availability: compares against the raw-minted stored ip_index; "
        "changing only this side would invalidate live sessions. Paired with "
        "the _create_session exception above.",
    ),
    RawIpException(
        "live session verifier binding index",
        "src/core/auth/managers/session/verification.py",
        "_verify_session_token",
        "Availability: same raw-binding comparison as the lifecycle verifier; "
        "the stored index was minted raw, so the comparison stays raw.",
    ),
    RawIpException(
        "WS ticket binding index",
        "src/core/auth/managers/session/tickets.py",
        "_verify_ws_ticket",
        "Availability: a gateway ticket inherits its session's raw-minted "
        "ip_index, so the exchange must hash the presented address the same "
        "raw way as _create_session/_verify_session_token. Canonicalizing only "
        "this side would not match stored raw indexes and would wrongly reject "
        "bound tickets.",
    ),
    RawIpException(
        "WS ticket binding index (LifecycleMixin mirror)",
        "src/core/auth/managers/session/lifecycle.py",
        "_verify_ws_ticket",
        "Availability: peers mirrored TicketMixin._verify_ws_ticket into "
        "LifecycleMixin so an MRO reorder or direct caller cannot bypass the "
        "binding gate. It compares against the session's raw-minted ip_index, "
        "so the presented address must be hashed the same raw way as "
        "_create_session/_verify_session_token; canonicalizing only this side "
        "would not match stored raw indexes and would wrongly reject bound "
        "tickets.",
    ),
    RawIpException(
        "refresh-session binding index",
        "src/core/auth/managers/session/lifecycle.py",
        "refresh_session",
        "Availability: the N644 refresh gate compares the presented IP against "
        "the session's raw-minted auth_sessions.ip_index before consuming the "
        "bearer. Hashing a canonical form here while mint/verify stay raw would "
        "reject legitimate refreshes (and break the symmetry that stops a "
        "stolen refresh bearer from another host rotating the victim's "
        "credential). Not a mutable-state rate/budget/blacklist key.",
    ),
    RawIpException(
        "2FA challenge creation binding index",
        "src/core/auth/managers/twofactor.py",
        "_create_2fa_challenge",
        "Contested 2FA file (concurrently edited by another agent) plus "
        "availability: challenge ip_index is minted raw and compared raw on "
        "completion; canonicalizing one side breaks in-flight challenges. "
        "Left for the file owner to migrate both sides together.",
    ),
    RawIpException(
        "2FA challenge completion binding index",
        "src/core/auth/managers/twofactor.py",
        "complete_2fa",
        "Contested 2FA file plus availability: paired raw comparison with "
        "_create_2fa_challenge; must change atomically with the mint side.",
    ),
)


def _find_function(tree: ast.AST, func: str) -> ast.AST:
    matches = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
    ]
    assert len(matches) == 1, f"expected exactly one {func!r}, found {len(matches)}"
    return matches[0]


def _assert_site(site: IpKeySite, source: str) -> None:
    fn = _find_function(ast.parse(source), site.func)
    body = ast.unparse(fn)
    for marker in site.must_contain:
        assert marker in body, f"{site.label}: marker {marker!r} missing from {site.func}"
    if site.mode == "canon":
        assert any(marker in body for marker in CANON_MARKERS), (
            f"{site.label}: {site.func} no longer canonicalizes its IP key "
            "(expected one of " + ", ".join(CANON_MARKERS) + ")"
        )
    elif site.mode == "downstream":
        assert any(
            call in body for call in ("acheck_rate_limit", "check_rate_limit")
        ), f"{site.label}: {site.func} no longer routes its IP through the limiter"
    else:
        raise AssertionError(f"{site.label}: unknown mode {site.mode!r}")


# ---------------------------------------------------------------------------
# Part 2: whole-tree sweep for NEW raw blind-index IP keys
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawKeyFinding:
    path: str
    func: str
    lineno: int
    call: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno} in {self.func}(): {self.call}"


def _raw_ip_key_candidates(
    tree: ast.AST,
    *,
    path: str,
    exceptions: tuple[RawIpException, ...] = (),
) -> list[RawKeyFinding]:
    """Raw blind-index IP derivations in one module, minus documented ones."""
    allowed = {(e.rel_path, e.func) for e in exceptions}
    found: list[RawKeyFinding] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if (path, fn.name) in allowed:
            continue
        safe_names = _canonicalized_names(fn)
        for call in ast.walk(fn):
            if not isinstance(call, ast.Call) or _call_name(call) not in IP_HASH_FUNCS:
                continue
            if not _is_ip_domain_call(call):
                continue
            if any(marker in ast.unparse(call) for marker in CANON_MARKERS):
                continue  # canonicalized inline in the argument
            ip_domain = any(
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value.strip() in IP_DOMAIN_LABELS
                for arg in call.args
            )
            raw = False
            for arg in call.args:
                if isinstance(arg, ast.Constant):
                    continue  # the domain label itself, never the key value
                if not (ip_domain or _has_ip_hint(arg)):
                    continue
                if isinstance(arg, ast.Name) and arg.id in safe_names:
                    continue  # value came from a canonicalizer alias
                if any(marker in ast.unparse(arg) for marker in CANON_MARKERS):
                    continue
                # A value hashed under an IP domain with no canonicalization
                # evidence fails closed even if its variable name is novel.
                raw = True
            if raw:
                found.append(
                    RawKeyFinding(path, fn.name, call.lineno, ast.unparse(call))
                )
    return found


def _sweep(commit: str, exceptions: tuple[RawIpException, ...]) -> list[RawKeyFinding]:
    reader = _make_blob_reader(commit)
    findings: list[RawKeyFinding] = []
    for path in _production_files(commit):
        source = reader(path)
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise AssertionError(f"cannot parse committed {path}: {exc}") from exc
        findings.extend(_raw_ip_key_candidates(tree, path=path, exceptions=exceptions))
    return findings


# ---------------------------------------------------------------------------
# Self-tests: a detector that never fires must not let the real scan pass
# ---------------------------------------------------------------------------


def test_self_test_raw_fast_blind_index_ip_key_is_flagged():
    source = textwrap.dedent(
        """
        class C:
            def build_key(self, ip):
                return self.crypto.fast_blind_index(ip, "ip_address")
        """
    )
    findings = _raw_ip_key_candidates(
        ast.parse(source), path="src/api/_synthetic.py"
    )
    assert len(findings) == 1, findings
    assert "fast_blind_index" in findings[0].call
    # Also flagged with a novel variable name when the domain label gives it away.
    novel = textwrap.dedent(
        """
        class C:
            def novel_name(self, source_addr):
                return self.crypto.fast_blind_index(source_addr, "ip_address")
        """
    )
    assert len(
        _raw_ip_key_candidates(ast.parse(novel), path="src/api/_synthetic.py")
    ) == 1


def test_self_test_canonicalized_forms_are_accepted():
    source = textwrap.dedent(
        """
        import ipaddress
        from src.core.auth.ip_canonical import canonicalize_ip_address

        class C:
            def inline(self, ip_address):
                return self.crypto.fast_blind_index(
                    canonicalize_ip_address(ip_address) or ip_address, "ip_address"
                )

            def via_alias(self, ip_address):
                canonical_ip = canonicalize_ip_address(ip_address) or ip_address
                return self.crypto.fast_blind_index(canonical_ip, "ip_address")

            def via_helper(self, ip_address):
                canonical = _canonical_source_ip(ip_address)
                return self.crypto.blind_index(canonical, "ip_address") if canonical else None

            def via_ipaddress(self, ip_address):
                canonical = str(ipaddress.ip_address(ip_address))
                return self.crypto.blind_index(canonical, "ip_address")
        """
    )
    assert (
        _raw_ip_key_candidates(ast.parse(source), path="src/api/_synthetic.py") == []
    )


def test_self_test_registered_exceptions_do_not_hide_a_new_raw_ip_key():
    # Non-vacuity against the *real* allowlist: registering the relocated
    # binding sites above must not teach the sweep to ignore a new raw key.
    source = textwrap.dedent(
        """
        class C:
            def brand_new_security_key(self, ip_address):
                return self.crypto.fast_blind_index(ip_address, "ip_address")
        """
    )
    findings = _raw_ip_key_candidates(
        ast.parse(source),
        path="src/core/auth/managers/session/lifecycle.py",
        exceptions=DOCUMENTED_RAW_EXCEPTIONS,
    )
    assert len(findings) == 1, findings
    assert "brand_new_security_key" == findings[0].func
    assert "fast_blind_index" in findings[0].call


def test_self_test_documented_exception_suppresses_only_its_own_site():
    source = textwrap.dedent(
        """
        class C:
            def build_key(self, ip):
                return self.crypto.fast_blind_index(ip, "ip_address")
        """
    )
    tree = ast.parse(source)
    path = "src/api/_synthetic.py"
    assert len(_raw_ip_key_candidates(tree, path=path)) == 1
    allow = (
        RawIpException("synthetic", path, "build_key", "raw is intentional here"),
    )
    assert _raw_ip_key_candidates(tree, path=path, exceptions=allow) == []
    # A different function name under the same path is NOT suppressed.
    other = (
        RawIpException("synthetic", path, "some_other_func", "not this one"),
    )
    assert len(_raw_ip_key_candidates(tree, path=path, exceptions=other)) == 1


def test_self_test_registry_marker_check_fails_when_canonicalization_is_removed():
    site = IpKeySite(
        "synthetic audit site",
        "src/api/_synthetic.py",
        "_log_audit",
        ("fast_blind_index",),
        "canon",
        "test",
    )
    good = textwrap.dedent(
        """
        from src.core.auth.ip_canonical import canonicalize_ip_address
        def _log_audit(ip_address):
            canonical = canonicalize_ip_address(ip_address) or ip_address
            return self.crypto.fast_blind_index(canonical, "ip_address")
        """
    )
    bad = textwrap.dedent(
        """
        def _log_audit(ip_address):
            return self.crypto.fast_blind_index(ip_address, "ip_address")
        """
    )
    _assert_site(site, good)
    with pytest.raises(AssertionError, match="no longer canonicalizes"):
        _assert_site(site, bad)


def test_self_test_exception_registry_requires_reasons_and_real_sites():
    assert DOCUMENTED_RAW_EXCEPTIONS, "no documented exceptions registered"
    for exc in DOCUMENTED_RAW_EXCEPTIONS:
        assert exc.reason.strip(), f"{exc.label}: empty allowlist reason"
        assert exc.rel_path.startswith("src/"), exc.rel_path
        assert exc.func, exc.label


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    files = _production_files(commit_sha)
    assert len(files) >= 1000, f"only {len(files)} production modules scanned"
    for required in (
        "src/core/auth/managers/ip_blacklist.py",
        "src/core/auth/managers/session/login.py",
        "src/core/auth/managers/api_token.py",
        "src/core/auth/managers/base.py",
        "src/core/ratelimit/manager/principals.py",
        "src/api/routes/telemetry.py",
        "src/core/auth/managers/twofactor.py",
    ):
        assert required in files, f"{required} unexpectedly excluded from scan"
    # The reader really returns committed content: these contested files must
    # still contain their canonicalization markers in HEAD.
    reader = _make_blob_reader(commit_sha)
    assert "canonicalize_ip_address" in reader("src/core/auth/managers/base.py")
    assert "ipaddress.ip_address" in reader(
        "src/core/ratelimit/manager/principals.py"
    )


def test_documented_ip_key_sites_still_canonicalized_in_committed_tree(commit_sha):
    reader = _make_blob_reader(commit_sha)
    assert len(DOCUMENTED_IP_KEY_SITES) >= 11, "registry lost documented sites"
    for site in DOCUMENTED_IP_KEY_SITES:
        _assert_site(site, reader(site.rel_path))


def test_committed_tree_sweep_detector_is_non_vacuous(commit_sha):
    # With suppression removed, the detector must fire on the eight real raw
    # sites. If it fires nowhere, the "no new raw key" test below is vacuous.
    findings = _sweep(commit_sha, ())
    found = {(f.path, f.func) for f in findings}
    expected = {(e.rel_path, e.func) for e in DOCUMENTED_RAW_EXCEPTIONS}
    assert expected <= found, (
        "sweep failed to detect documented raw IP keys; detector is vacuous. "
        f"found={sorted(found)} expected={sorted(expected)}"
    )


def test_documented_raw_exceptions_are_exactly_the_sweep_findings(commit_sha):
    # The allowlist must stay minimal: every documented exception still has a
    # raw site, and every raw site in the tree is documented (no novel name).
    found = {(f.path, f.func) for f in _sweep(commit_sha, ())}
    expected = {(e.rel_path, e.func) for e in DOCUMENTED_RAW_EXCEPTIONS}
    assert found == expected, (
        "raw blind-index IP key set drifted from the documented allowlist.\n"
        f"  undocumented (NEW) raw keys: {sorted(found - expected)}\n"
        f"  stale exception entries: {sorted(expected - found)}"
    )


def test_no_new_raw_ip_blind_index_key_in_committed_tree(commit_sha):
    offenders = _sweep(commit_sha, DOCUMENTED_RAW_EXCEPTIONS)
    assert not offenders, (
        "New raw blind-index IP key(s) in production (canonicalize_ip_address "
        "or an ipaddress.ip_address normalizer is required before hashing):\n  "
        + "\n  ".join(str(o) for o in sorted(offenders, key=lambda o: (o.path, o.lineno)))
    )
