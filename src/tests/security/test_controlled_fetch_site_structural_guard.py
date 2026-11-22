"""Structural guard: every controlled-URL HTTP fetch is pinned, no-redirect, or documented.

Campaign background
-------------------
The redirect-SSRF campaign (edfd43d0 and its predecessors) hardened every seam
that fetches a URL an attacker, an operator or an external API can influence.
The surviving production patterns are:

* ``pinned_urlopen`` / ``resolve_*_target`` / ``pinned_request_target`` style
  seams: the URL is screened (scheme allowlist + private/reserved-IP rejection)
  once and the connection goes to the *approved* IP, so a 3xx or a rebinding
  answer cannot steer a second lookup to an internal address;
* no-redirect + per-hop revalidation (``allow_redirects=False``,
  ``follow_redirects=False``, urllib3 ``redirect=False``): the client never
  auto-follows, and every hop is re-screened through ``parse_outbound_url`` /
  ``URLValidator`` / ``resolve_redirect_target`` before it is contacted;
* fixed (hardcoded) URLs, including operator/deployment config passed as a
  trusted base and CLI-only localhost probes.

Runtime tests cover each seam individually, but a later edit can drop one
no-redirect keyword or add a brand-new ``requests.get(user_url)`` with the
default redirect-following and no IP screen while every focused test still
passes. These contests are exactly why the audit reads blobs straight out of
COMMITTED HEAD (``git show HEAD:<path>``): a peer's stale working tree cannot
mask a committed regression, and this file only ever inspects committed code.

Design (mirrors ``test_ip_canonicalization_structural_guard.py``)
-----------------------------------------------------------------
1. :data:`DOCUMENTED_FETCH_SITES` enumerates every production HTTP-client fetch
   site whose URL is not a hardcoded literal. Each entry records the evidence
   (markers that must remain in that function body) and a written reason for
   why the site is safe. The whole registry is checked against committed blobs.

2. A whole-tree AST sweep of every production module that imports an HTTP
   library flags any *new* controlled-URL fetch whose call site carries neither
   a pin/IP-screen marker nor a no-redirect control -- i.e. default
   redirect-following with no IP screen. The only suppressed sites are in
   :data:`ALLOWLISTED_FETCH_SITES`, each with a reviewed reason (caller-side
   pinning, operator-fixed trusted base, or CLI-only localhost probe).

3. Non-vacuity is proven three ways: synthetic ``requests.get(user_url)`` is
   flagged while fixed/pinned/no-redirect forms are accepted; the detector must
   fire on the real documented allowlist sites when suppression is removed;
   and a throwaway git repo proves the reader returns committed content, not
   the working tree.

No app import, config, DB, or network is needed.
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


def _git(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
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


def _make_blob_reader(commit: str, cwd: Path = ROOT):
    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}", cwd=cwd)
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
# Vocabulary
# ---------------------------------------------------------------------------

# Modules whose callables are genuine outbound HTTP clients. ``src.utils.outbound``
# is included only so its pinned seams can be *recognised and excluded* below.
HTTP_MODULES = frozenset(
    {
        "httpx",
        "requests",
        "aiohttp",
        "urllib.request",
        "urllib3",
        "urllib3.connectionpool",
        "urllib3.poolmanager",
        "src.utils.outbound",
    }
)
HTTP_METHODS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "request",
        "urlopen",
        "stream",
        "send",
        "open",
    }
)
CLIENT_CONSTRUCTORS = frozenset(
    {
        "Client",
        "AsyncClient",
        "Session",
        "ClientSession",
        "PoolManager",
        "HTTPConnectionPool",
        "HTTPSConnectionPool",
    }
)

# Evidence that the URL was screened / pinned before (or instead of) connect.
PIN_MARKERS: tuple[str, ...] = (
    "pinned_urlopen",
    "resolve_remote_target",
    "resolve_redirect_target",
    "pinned_request_target",
    "_pinned_target",
    "_build_target",
    "bracket_ip_literal",
    "validate_url_for_request",
    "parse_outbound_url",
    "resolve_outbound_url",
    "require_https_url",
    "validate_delivery_endpoint",
    "_validate_redirect",
    "_validate_azure",
)
# Evidence that the transport will not auto-follow a 3xx.
NO_REDIRECT_MARKERS: tuple[str, ...] = (
    "allow_redirects=False",
    "follow_redirects=False",
    "redirect=False",
    "retries=False",
    "_NoRedirect",
)
NO_REDIRECT_KEYWORDS = frozenset(
    {"allow_redirects", "follow_redirects", "redirect", "retries"}
)


# ---------------------------------------------------------------------------
# Registry of documented controlled-URL fetch sites (committed evidence)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchSite:
    """A production fetch site whose URL is not a hardcoded literal.

    ``must_contain`` markers must all appear in ``func``'s body in the committed
    blob. ``mode`` is a short classification; ``why`` records the safety reason.
    """

    label: str
    rel_path: str
    func: str
    mode: str
    must_contain: tuple[str, ...]
    why: str


DOCUMENTED_FETCH_SITES: tuple[FetchSite, ...] = (
    FetchSite(
        "OAuth token/user-info exchange",
        "src/api/routes/auth/oauth.py",
        "oauth_callback",
        "validated",
        ("require_https_url", "httpx.AsyncClient", "client.post", "client.get"),
        "Provider endpoints come from the pinned OAUTH_PROVIDERS table and are "
        "run through require_https_url (https + no credentials/backslash) "
        "before the httpx request; the remaining user-info URLs are literals.",
    ),
    FetchSite(
        "CLI selftest readiness probe",
        "src/cli/selftest.py",
        "_wait_until_ready",
        "allowlisted",
        ("requests.get", "http://"),
        "CLI-only readiness probe against the operator-specified host/port of "
        "the server being started; not a request handler SSRF surface.",
    ),
    FetchSite(
        "CLI status liveness probe",
        "src/cli/status_probe.py",
        "run_status_checks",
        "allowlisted",
        ("requests.Session", "session.request", "STATUS_CHECKS"),
        "CLI-only read-only status probe against an operator-supplied base_url "
        "with a fixed path table; not a request handler SSRF surface.",
    ),
    FetchSite(
        "federation artifact signed POST",
        "src/core/artifacts/federation.py",
        "_post_with_retry",
        "pinned",
        (
            "resolve_remote_target",
            "pinned_request_target",
            "httpx.Client",
            "client.post",
        ),
        "Remote instance URL is admin-approved but can originate in an inbound "
        "join request; resolve_remote_target + pinned_request_target screen and "
        "pin the address before the httpx POST.",
    ),
    FetchSite(
        "Azure transcription pinned fetch",
        "src/core/artifacts/transcription/provider.py",
        "_pinned_azure_get",
        "pinned",
        ("pinned_request_target", "httpx.get", "extensions"),
        "Recording URLs are persisted from user content; parts are validated "
        "and pinned_request_target connects to the approved IP with SNI/Host "
        "preserved, so no second DNS lookup can be rebound.",
    ),
    FetchSite(
        "Azure transcription REST create/status",
        "src/core/artifacts/transcription/provider.py",
        "_transcribe_rest",
        "no_redirect",
        ("allow_redirects=False", "requests.post", "requests.get", "_pinned_azure_get"),
        "Base URL is operator config; requests would follow a 3xx and replay "
        "the recording body, so every call sets allow_redirects=False and the "
        "user-supplied recording URLs go through the pinned helper.",
    ),
    FetchSite(
        "OpenAI transcription blocking POST",
        "src/core/artifacts/transcription/provider.py",
        "_transcribe_blocking",
        "no_redirect",
        ("allow_redirects=False", "requests.post"),
        "Endpoint is validated by the adapter config gate; allow_redirects=False "
        "stops a 3xx from replaying the multipart recording body at an internal "
        "address.",
    ),
    FetchSite(
        "link-preview streamed fetch with manual redirects",
        "src/core/embeds/link_preview.py",
        "_fetch_with_redirects",
        "no_redirect",
        (
            "follow_redirects=False",
            "_validate_redirect",
            "bracket_ip_literal",
            "client.stream",
        ),
        "User-supplied URL; httpx auto-follow is disabled and each 3xx is "
        "re-screened via _validate_redirect and re-pinned (Host/SNI) before "
        "the next hop is contacted.",
    ),
    FetchSite(
        "PlexiJoin connection health check",
        "src/core/plexijoin/manager.py",
        "test_connection",
        "pinned",
        ("resolve_remote_target", "_pinned_target", "httpx.AsyncClient", "client.get"),
        "Stored remote_url is SSRF-validated and the request is pinned to the "
        "approved IP before the httpx GET.",
    ),
    FetchSite(
        "PlexiJoin signed POST",
        "src/core/plexijoin/manager.py",
        "_post_signed",
        "pinned",
        ("resolve_remote_target", "_pinned_target", "httpx.AsyncClient", "client.post"),
        "Signed federation POST uses the same resolve + pin seam as the health "
        "check, so a rebinding peer cannot redirect delivery internally.",
    ),
    FetchSite(
        "APNs push POST",
        "src/core/push/manager/apns.py",
        "_send_apns",
        "validated",
        ("require_https_url", "client.post"),
        "The APNs host is a trusted literal selected by the sandbox flag and "
        "run through require_https_url; only the device token (strictly "
        "validated) is appended to the path.",
    ),
    FetchSite(
        "media proxy validated fetch",
        "src/core/media/security/proxy.py",
        "_fetch_url",
        "no_redirect",
        ("pool.urlopen", "redirect=False", "bracket_ip_literal", "HTTPConnectionPool"),
        "The caller chain (fetch -> _fetch_validated) screens the URL with "
        "URLValidator and passes the approved hostname/ip here; _fetch_url "
        "connects to that ip with Host/SNI preserved and urllib3 redirect "
        "following disabled so a 3xx cannot pivot internally.",
    ),
    FetchSite(
        "webhook delivery POST helper",
        "src/core/webhooks/delivery.py",
        "_post",
        "allowlisted",
        ("session.post",),
        "_post is a private helper fed only by deliver(), which re-validates "
        "the endpoint with validate_delivery_endpoint, pins it with "
        "_build_target, and creates the session with follow_redirects=False; "
        "each redirect hop is re-validated via resolve_redirect_target.",
    ),
    FetchSite(
        "Janus SFU REST request",
        "src/core/voice/signaling/sfu/janus.py",
        "_request",
        "allowlisted",
        ("session.post", "_api_url", "httpx.TimeoutException"),
        "Operator/deployment-configured trusted SFU base (default "
        "http://localhost:8088/janus) plus fixed endpoint constants; httpx does "
        "not follow redirects by default, and no user input reaches the host.",
    ),
    FetchSite(
        "Mediasoup SFU REST request",
        "src/core/voice/signaling/sfu/mediasoup.py",
        "_request",
        "allowlisted",
        ("session.request", "_api_url", "httpx.TimeoutException"),
        "Operator/deployment-configured trusted SFU base (default "
        "http://localhost:3000) plus fixed endpoint constants; httpx does not "
        "follow redirects by default, and no user input reaches the host.",
    ),
    FetchSite(
        "static-client redirect follower",
        "src/static_client/fetcher.py",
        "_follow_safe_redirects",
        "no_redirect",
        ("allow_redirects=False", "parse_outbound_url", "requests.get"),
        "requests auto-follow is disabled; each Location is re-screened with "
        "parse_outbound_url and credential headers are dropped on cross-host "
        "hops before the next get.",
    ),
    FetchSite(
        "static-client streaming download",
        "src/static_client/fetcher.py",
        "_download_to_stream",
        "no_redirect",
        ("allow_redirects=False", "requests.get", "_follow_safe_redirects"),
        "Initial get disables redirects and delegates any 3xx to the validated "
        "redirect follower.",
    ),
    FetchSite(
        "static-client release listing",
        "src/static_client/fetcher.py",
        "list_releases",
        "no_redirect",
        ("allow_redirects=False", "requests.get", "_follow_safe_redirects"),
        "Release listing get disables redirects and delegates 3xx handling to "
        "the validated redirect follower.",
    ),
)


@dataclass(frozen=True)
class FetchException:
    """A controlled fetch whose call site lacks local controls but is reviewed."""

    label: str
    rel_path: str
    func: str
    reason: str


# The ONLY controlled fetch sites allowed to lack a pin/IP-screen marker or a
# no-redirect keyword at the call site. Every new site fails closed until it is
# reviewed and added here with a reason.
ALLOWLISTED_FETCH_SITES: tuple[FetchException, ...] = tuple(
    FetchException(site.label, site.rel_path, site.func, site.why)
    for site in DOCUMENTED_FETCH_SITES
    if site.mode == "allowlisted"
)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _find_functions(tree: ast.AST, func: str) -> tuple[ast.AST, ...]:
    """All definitions named ``func`` (some adapters use the same method name)."""
    matches = tuple(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
    )
    assert matches, f"no definition of {func!r} found"
    return matches


def _import_origins(tree: ast.AST) -> dict[str, str]:
    """Map a local name to the HTTP module it was imported from."""
    origins: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in HTTP_MODULES:
                    origins[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in HTTP_MODULES:
            for alias in node.names:
                origins[alias.asname or alias.name] = node.module
    return origins


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _client_variable_names(tree: ast.AST) -> set[str]:
    """Names bound to an HTTP client constructor, plus conventional names."""
    names: set[str] = {"client", "session", "pool"}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in CLIENT_CONSTRUCTORS
        ):
            continue
        for parent in ast.walk(tree):
            if (
                not isinstance(parent, (ast.Assign, ast.AnnAssign))
                or parent.value is not node
            ):
                continue
            targets = (
                parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Attribute):
                    names.add(target.attr)
    return names


def _literal_url_constants(tree: ast.AST) -> set[str]:
    """Simple names assigned a hardcoded http(s) literal (fixed URLs)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        value = node.value
        if not (
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and value.value.startswith(("http://", "https://"))
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _is_literal_url(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(("http://", "https://"))
    )


def _url_argument(call: ast.Call, module: str, attr: str) -> ast.AST | None:
    """Locate the URL argument for the various client signatures."""
    # urllib3 pool.urlopen(method, url, ...) carries the URL as the second arg.
    if attr == "urlopen" and module.startswith("urllib3") and len(call.args) >= 2:
        return call.args[1]
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == "url":
            return keyword.value
    return None


def _enclosing_functions(tree: ast.AST) -> dict[int, ast.AST]:
    """Map each call node id to the nearest enclosing function (or None)."""
    result: dict[int, ast.AST] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(fn):
                if isinstance(child, ast.Call):
                    result[id(child)] = fn
    return result


def _compact(source: str) -> str:
    return source.replace(" ", "").replace("\n", "")


def _has_controls(
    call: ast.Call, fn: ast.AST | None, fn_src: str, call_src: str
) -> bool:
    """True if the fetch is screened/pinned or cannot auto-follow a redirect."""
    compact_fn = _compact(fn_src)
    compact_call = _compact(call_src)
    # Pin / IP-screen seam used in the same function as the fetch.
    if any(marker in fn_src or marker in call_src for marker in PIN_MARKERS):
        return True
    # Transport cannot follow a 3xx (call-level keyword or factory setting).
    if any(
        marker in compact_fn or marker in compact_call for marker in NO_REDIRECT_MARKERS
    ):
        return True
    for keyword in call.keywords:
        if (
            keyword.arg in NO_REDIRECT_KEYWORDS
            and getattr(keyword.value, "value", None) is False
        ):
            return True
    return False


@dataclass(frozen=True)
class FetchFinding:
    path: str
    func: str
    lineno: int
    call: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno} in {self.func}(): {self.call}"


def _controlled_fetch_calls(
    tree: ast.AST,
    *,
    path: str,
    exceptions: tuple[FetchException, ...] = (),
) -> list[FetchFinding]:
    """Controlled-URL HTTP fetch calls lacking a local pin/no-redirect control."""
    origins = _import_origins(tree)
    if not origins:
        return []
    clients = _client_variable_names(tree)
    fixed_names = _literal_url_constants(tree)
    fn_of = _enclosing_functions(tree)
    allowed = {(e.rel_path, e.func) for e in exceptions}
    findings: list[FetchFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        module: str | None = None
        attr: str | None = None
        if isinstance(func, ast.Attribute):
            attr = func.attr
            root = _root_name(func)
            if root in origins:
                module = origins[root]
            elif root in clients:
                module = "CLIENT"
            else:
                continue
        elif isinstance(func, ast.Name):
            if func.id not in origins:
                continue
            module = origins[func.id]
            attr = func.id
        else:
            continue
        if attr not in HTTP_METHODS:
            continue
        # The vetted transport / pin seams are the fix, never a finding.
        if module and module.startswith("src.utils.outbound"):
            continue
        url_node = _url_argument(node, module, attr)
        if _is_literal_url(url_node):
            continue  # fixed URL (category b)
        if isinstance(url_node, ast.Name) and url_node.id in fixed_names:
            continue  # fixed URL via a simple literal constant
        fn = fn_of.get(id(node))
        func_name = fn.name if fn is not None else "<module>"
        if (path, func_name) in allowed:
            continue  # reviewed allowlist entry (category a)
        fn_src = ast.unparse(fn) if fn is not None else ""
        call_src = ast.unparse(node)
        if _has_controls(node, fn, fn_src, call_src):
            continue  # pinned or no-redirect (categories a/c)
        findings.append(FetchFinding(path, func_name, node.lineno, call_src[:90]))
    return findings


def _all_controlled_calls(tree: ast.AST, *, path: str) -> set[tuple[str, str]]:
    """Every (path, func) with a controlled-URL HTTP fetch, guarded or not."""
    origins = _import_origins(tree)
    if not origins:
        return set()
    clients = _client_variable_names(tree)
    fixed_names = _literal_url_constants(tree)
    fn_of = _enclosing_functions(tree)
    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        module: str | None = None
        attr: str | None = None
        if isinstance(func, ast.Attribute):
            attr = func.attr
            root = _root_name(func)
            if root in origins:
                module = origins[root]
            elif root in clients:
                module = "CLIENT"
            else:
                continue
        elif isinstance(func, ast.Name):
            if func.id not in origins:
                continue
            module = origins[func.id]
            attr = func.id
        else:
            continue
        if attr not in HTTP_METHODS or (
            module and module.startswith("src.utils.outbound")
        ):
            continue
        url_node = _url_argument(node, module, attr)
        if _is_literal_url(url_node):
            continue
        if isinstance(url_node, ast.Name) and url_node.id in fixed_names:
            continue
        fn = fn_of.get(id(node))
        found.add((path, fn.name if fn is not None else "<module>"))
    return found


def _sweep(commit: str, exceptions: tuple[FetchException, ...]) -> list[FetchFinding]:
    reader = _make_blob_reader(commit)
    findings: list[FetchFinding] = []
    for path in _production_files(commit):
        source = reader(path)
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise AssertionError(f"cannot parse committed {path}: {exc}") from exc
        findings.extend(_controlled_fetch_calls(tree, path=path, exceptions=exceptions))
    return findings


def _sweep_all(commit: str) -> set[tuple[str, str]]:
    reader = _make_blob_reader(commit)
    found: set[tuple[str, str]] = set()
    for path in _production_files(commit):
        tree = ast.parse(reader(path))
        found |= _all_controlled_calls(tree, path=path)
    return found


# ---------------------------------------------------------------------------
# Self-tests: prove the detectors fire and are not vacuous
# ---------------------------------------------------------------------------


def test_synthetic_unguarded_controlled_fetch_is_flagged():
    """A brand-new requests.get(user_url) with default redirect is flagged."""
    source = textwrap.dedent(
        """
        import requests

        def handler(user_url):
            return requests.get(user_url, timeout=5)
        """
    )
    findings = _controlled_fetch_calls(ast.parse(source), path="src/api/_synthetic.py")
    assert len(findings) == 1, findings
    assert "requests.get" in findings[0].call
    assert findings[0].func == "handler"


def test_synthetic_fixed_and_pinned_fetches_are_accepted():
    """Fixed literals and pinned/no-redirect seams never raise a finding."""
    fixed = textwrap.dedent(
        """
        import requests
        API = "https://api.example.com/v1"

        def fixed_fetch():
            requests.get("https://api.example.com/health", timeout=5)
            requests.get(API, timeout=5)
        """
    )
    assert _controlled_fetch_calls(ast.parse(fixed), path="src/api/_synthetic.py") == []

    pinned = textwrap.dedent(
        """
        from src.utils.outbound import pinned_urlopen
        from src.core.plexijoin.manager import resolve_remote_target

        def pinned_fetch(user_url):
            return pinned_urlopen(user_url)

        def resolved_fetch(user_url):
            hostname, ip = resolve_remote_target(user_url)
            return requests.get(user_url, allow_redirects=False, timeout=5)
        """
    )
    assert (
        _controlled_fetch_calls(ast.parse(pinned), path="src/api/_synthetic.py") == []
    )


def test_synthetic_allowlist_suppression_is_site_scoped():
    source = textwrap.dedent(
        """
        import requests

        def _post(user_url):
            return requests.get(user_url)
        """
    )
    tree = ast.parse(source)
    path = "src/api/_synthetic.py"
    assert len(_controlled_fetch_calls(tree, path=path)) == 1
    allow = (FetchException("synthetic", path, "_post", "reviewed"),)
    assert _controlled_fetch_calls(tree, path=path, exceptions=allow) == []
    other = (FetchException("synthetic", path, "some_other_func", "not this one"),)
    assert len(_controlled_fetch_calls(tree, path=path, exceptions=other)) == 1


def test_exception_registry_requires_reasons():
    assert ALLOWLISTED_FETCH_SITES, "no documented allowlist entries"
    for exc in ALLOWLISTED_FETCH_SITES:
        assert exc.reason.strip(), f"{exc.label}: empty allowlist reason"
        assert exc.rel_path.startswith("src/"), exc.rel_path
        assert exc.func, exc.label


def test_registry_is_well_formed():
    keys = [(s.rel_path, s.func) for s in DOCUMENTED_FETCH_SITES]
    assert keys, "registry must not be empty"
    assert len(keys) == len(set(keys)), "duplicate fetch sites in registry"
    for site in DOCUMENTED_FETCH_SITES:
        assert site.must_contain, f"{site.label}: no evidence markers"
        assert site.why.strip(), f"{site.label}: undocumented safety reason"
        assert site.mode in {"pinned", "no_redirect", "validated", "allowlisted"}
    # Every allowlist entry must also be a documented registry site.
    documented = set(keys)
    for exc in ALLOWLISTED_FETCH_SITES:
        assert (exc.rel_path, exc.func) in documented


def test_blob_reader_returns_committed_content_not_working_tree(tmp_path):
    """Non-vacuity of the HEAD reader: a divergent working tree is ignored."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    target = "src/sample.py"
    committed = "VALUE = 'committed'\n"
    (repo / "src").mkdir()
    (repo / target).write_text(committed, encoding="utf-8")
    _git("add", target, cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    (repo / target).write_text("VALUE = 'working'\n", encoding="utf-8")
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    reader = _make_blob_reader(sha, cwd=repo)
    blob = reader(target)
    assert "committed" in blob and "working" not in blob, (
        "blob reader returned the divergent working tree instead of the commit"
    )
    # A working-tree-only file is invisible to the same reader.
    (repo / "src" / "extra.py").write_text("X = 1\n", encoding="utf-8")
    proc = _git("show", f"{sha}:src/extra.py", cwd=repo)
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    files = _production_files(commit_sha)
    assert len(files) >= 1000, f"only {len(files)} production modules scanned"
    for required in (
        "src/core/artifacts/transcription/provider.py",
        "src/core/plexijoin/manager.py",
        "src/core/webhooks/delivery.py",
        "src/core/media/security/proxy.py",
        "src/static_client/fetcher.py",
        "src/core/voice/signaling/sfu/janus.py",
    ):
        assert required in files, f"{required} unexpectedly excluded from scan"
    reader = _make_blob_reader(commit_sha)
    # These contested files must still carry their controls in HEAD.
    assert "allow_redirects=False" in reader(
        "src/core/artifacts/transcription/provider.py"
    )
    assert "resolve_remote_target" in reader("src/core/plexijoin/manager.py")


def test_documented_fetch_sites_retain_their_evidence(commit_sha):
    reader = _make_blob_reader(commit_sha)
    for site in DOCUMENTED_FETCH_SITES:
        tree = ast.parse(reader(site.rel_path))
        bodies = [ast.unparse(fn) for fn in _find_functions(tree, site.func)]
        for marker in site.must_contain:
            assert any(marker in body for body in bodies), (
                f"{site.label}: evidence {marker!r} missing from {site.rel_path}::{site.func}"
            )


def test_caller_side_ssrf_gates_are_still_present(commit_sha):
    """Some fetch sites validate in a caller that has no HTTP call itself."""
    reader = _make_blob_reader(commit_sha)
    proxy = ast.unparse(
        _find_functions(ast.parse(reader("src/core/media/security/proxy.py")), "fetch")[
            0
        ]
    )
    assert "validate_url_for_request" in proxy, "media proxy lost its URLValidator gate"
    delivery_src = reader("src/core/webhooks/delivery.py")
    deliver = ast.unparse(_find_functions(ast.parse(delivery_src), "deliver")[0])
    for marker in (
        "validate_delivery_endpoint",
        "_build_target",
        "resolve_redirect_target",
    ):
        assert marker in deliver, f"webhook driver lost its gate: {marker}"
    session_fn = ast.unparse(_find_functions(ast.parse(delivery_src), "_session")[0])
    assert "follow_redirects=False" in session_fn, (
        "webhook session may auto-follow redirects"
    )
    # The SFU clients rely on httpx's default no-redirect for their fixed base.
    for rel in (
        "src/core/voice/signaling/sfu/janus.py",
        "src/core/voice/signaling/sfu/mediasoup.py",
    ):
        sfu_src = reader(rel)
        assert "httpx.AsyncClient(" in sfu_src, f"{rel}: SFU client constructor missing"
        assert "follow_redirects=True" not in sfu_src, (
            f"{rel}: SFU client auto-follows redirects"
        )


def test_registry_enumerates_every_controlled_fetch_site(commit_sha):
    found = _sweep_all(commit_sha)
    documented = {(s.rel_path, s.func) for s in DOCUMENTED_FETCH_SITES}
    assert documented <= found, (
        "registry names sites that are no longer controlled fetches (stale entries): "
        f"{sorted(documented - found)}"
    )
    assert found <= documented, (
        "NEW controlled-URL fetch site(s) missing from the registry:\n  "
        + "\n  ".join(f"{p}::{fn}" for p, fn in sorted(found - documented))
    )


def test_sweep_detector_fires_on_the_real_documented_sites(commit_sha):
    """With suppression removed the sweep must find the allowlisted sites."""
    findings = _sweep(commit_sha, ())
    found = {(f.path, f.func) for f in findings}
    expected = {(e.rel_path, e.func) for e in ALLOWLISTED_FETCH_SITES}
    assert expected <= found, (
        "sweep failed to detect real allowlisted fetch sites; detector is vacuous. "
        f"found={sorted(found)} expected={sorted(expected)}"
    )


def test_allowlist_is_exactly_the_sweep_findings(commit_sha):
    found = {(f.path, f.func) for f in _sweep(commit_sha, ())}
    expected = {(e.rel_path, e.func) for e in ALLOWLISTED_FETCH_SITES}
    assert found == expected, (
        "controlled-fetch allowlist drifted from the committed tree.\n"
        f"  undocumented (NEW) uncontrolled fetches: {sorted(found - expected)}\n"
        f"  stale allowlist entries: {sorted(expected - found)}"
    )


def test_no_new_unguarded_controlled_fetch_in_committed_tree(commit_sha):
    offenders = _sweep(commit_sha, ALLOWLISTED_FETCH_SITES)
    assert not offenders, (
        "New controlled-URL HTTP fetch with default redirect-following and no "
        "IP screen (pin the target with pinned_urlopen/resolve_*_target or set "
        "no-redirect + per-hop revalidation, then document it in the registry):\n  "
        + "\n  ".join(
            str(o) for o in sorted(offenders, key=lambda o: (o.path, o.lineno))
        )
    )
