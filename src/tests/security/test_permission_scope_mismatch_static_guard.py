"""Static guard: a permission check must resolve at the permission's catalog scope.

``72d3f648`` fixed a permission-scope escalation: ``channels.manage`` (a
server-scoped permission) was checked with a ``channel_id`` at six channel-write
sites. Because the permission resolver applies per-channel overrides whenever a
channel id is supplied, a member granted ``channels.manage`` in ONE channel
could delete/move/edit that channel or rewrite its overrides -- a channel-only
grant authorising a destructive server-level write. The fix removed the channel
argument so those checks resolve at server scope.

Runtime regressions cover the six fixed sites, but nothing stops the same
pattern from being reintroduced (or copied) into a *new* call site. This guard
closes that gap by reading the source instead of executing it:

1. INTENDED SCOPE FROM THE CATALOG -- ``SERVER_PERMISSIONS`` in
   ``src/core/servers/models.py`` is parsed with :mod:`ast`. The catalog itself
   documents scope: its ``# ... (per channel)`` section marks channel-scoped
   permissions (the ``messages.*`` family). Every other catalog entry is
   server-scoped. The derivation is asserted to be non-empty in both directions
   and to fail loudly if the catalog loses its scope marker.

2. ENUMERATION -- every ``has_permission`` / ``require_permission`` /
   ``can_manage_*`` call under ``src/core`` and ``src/api`` is walked (with
   accurate enclosing-function ancestors). A call's scope is the presence of a
   channel argument (4th positional ``channel_id`` or ``channel_id=`` /
   ``cid=`` keyword); absence means server scope. The permission is taken from
   the literal argument, a ``permission=`` keyword, or a ``for permission in
   ("a", "b")`` enum literal (so the slowmode loop that was fixed is covered).
   Calls whose permission cannot be resolved to a catalog key are *skipped* --
   they are wrappers/forwarders, not gates -- which keeps false positives low.

3. MISMATCH + ALLOWLIST -- a check whose scope differs from its catalog scope
   is a finding, unless its exact ``(path, function, call, permission, scope)``
   key is in :data:`DOCUMENTED_MISMATCHES` with a written reason. The key
   includes the path and function, so a NEW mismatch at a novel call site is
   never silently allowed: it fails closed.

4. NON-VACUITY -- the real-tree scan must enumerate a substantial number of
   checks, and parametrized self-tests feed synthetic source through the very
   same analyser to prove it flags the canonical bug, the reverse mismatch, the
   loop variable form, and that it accepts correct scope and documented cases.

No files are mutated; all self-test inputs are in-memory strings.
"""

import ast
import re
import textwrap
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]
CATALOG_PATH = SRC / "core" / "servers" / "models.py"
SCAN_ROOTS = (SRC / "core", SRC / "api")

CHECK_NAMES = frozenset({"has_permission", "require_permission"})
MANAGE_PREFIX = "can_manage_"
CHANNEL_KWARGS = frozenset({"channel_id", "cid"})
CHANNEL_SECTION_MARKER = "per channel"

_CATALOG_KEY_RE = re.compile(r'^\s*"([A-Za-z0-9_.]+)"\s*:')
_SERVER = "server"
_CHANNEL = "channel"


@dataclass(frozen=True)
class Finding:
    """A permission check resolved at a scope other than its catalog scope."""

    path: str
    function: str
    call: str
    permission: str
    scope: str
    intended: str

    @property
    def key(self):
        return (self.path, self.function, self.call, self.permission, self.scope)

    def describe(self) -> str:
        return (
            f"{self.path}::{self.function} {self.call}(..., {self.permission!r}, "
            f"scope={self.scope}) but catalog scope={self.intended}"
        )


@dataclass
class Stats:
    """Enumeration counters -- proof the scan is not vacuous."""

    permission_checks: int = 0
    manage_calls: int = 0
    parsed_files: int = 0


# Documented, PRE-EXISTING scope mismatches. Each key is an exact
# (path, function, call, permission, scope) tuple so a new call site -- even one
# using the same permission -- is never covered by an existing entry. These
# were present before this guard; they are recorded here, not fixed, because
# they are non-authoritative visibility/participation checks (or deferred
# follow-ups), unlike the destructive channel-write gates fixed in 72d3f648.
DOCUMENTED_MISMATCHES = {
    (
        "api/routes/messages/messages_crud/broadcast.py",
        "_resolve_recipients",
        "has_permission",
        "channels.view",
        _CHANNEL,
    ): (
        "channels.view is a per-channel visibility gate: broadcast recipients "
        "must be filtered against each channel's view overrides. The catalog "
        "groups it under 'Channel management', but 'View channels' is evaluated "
        "per channel; channel scope is required to honour view grants/denials."
    ),
    (
        "core/events/gateway_emit.py",
        "resolve_channel_event_recipients",
        "has_permission",
        "channels.view",
        _CHANNEL,
    ): (
        "Channel-scoped gateway audience resolution: only members who can view "
        "THIS channel may receive the event. Intentional per-channel check."
    ),
    (
        "core/notifications/manager/creator.py",
        "_filter_recipients_by_channel_view",
        "has_permission",
        "channels.view",
        _CHANNEL,
    ): (
        "Notification fan-out filters recipients by visibility of the specific "
        "message channel; intentional per-channel check."
    ),
    (
        "core/servers/manager/base.py",
        "get_channel",
        "has_permission",
        "channels.view",
        _CHANNEL,
    ): (
        "get_channel resolves visibility for the requested channel id; the "
        "per-channel check is the point of the call, not an authorisation of a "
        "server-level write."
    ),
    (
        "core/voice/manager/channels.py",
        "get_voice_channel",
        "has_permission",
        "channels.view",
        _CHANNEL,
    ): (
        "Voice channel lookup checks visibility of that specific voice channel; "
        "intentional per-channel check."
    ),
    (
        "core/voice/manager/channels.py",
        "get_voice_channels",
        "has_permission",
        "channels.view",
        _CHANNEL,
    ): (
        "Voice channel listing pre-filters each row by its own channel view; "
        "intentional per-channel check."
    ),
    (
        "core/voice/signaling/manager/connection_mixin.py",
        "_require_connect_permission",
        "require_permission",
        "voice.connect",
        _CHANNEL,
    ): (
        "Joining a voice channel is a per-channel participation right evaluated "
        "against that channel's overrides; intentional."
    ),
    (
        "core/voice/signaling/manager/screenshare_mixin.py",
        "_require_stream_permission",
        "require_permission",
        "voice.speak",
        _CHANNEL,
    ): (
        "Screen-share/stream is a per-voice-channel participation right; "
        "intentional."
    ),
    (
        "core/notifications/manager/mention_validator.py",
        "validate_mentions",
        "has_permission",
        "messages.mention_everyone",
        _SERVER,
    ): (
        "Reverse-direction case: this branch runs only when the mention context "
        "has no channel id (server-only @everyone policy), so no channel scope "
        "exists to pass. Correct as written."
    ),
    (
        "core/servers/handlers/members/invites.py",
        "create_invite",
        "require_permission",
        "invites.create",
        _CHANNEL,
    ): (
        "Pre-existing check bounds invite creation to the channel being shared, "
        "so a per-channel invites.create grant is respected. Non-destructive "
        "check, documented here rather than changed; server-scope tightening is "
        "deferred. NOTE: the destructive channel-write gates (delete/update/move "
        "/overrides) are deliberately NOT allowlisted -- see 72d3f648."
    ),
}


@lru_cache(maxsize=1)
def catalog_scopes():
    """Derive {permission: scope} from the SERVER_PERMISSIONS catalog.

    The catalog is authoritative for both the permission universe and the
    scope split: an entry's nearest preceding section comment is its scope
    definition, and a section labelled ``(per channel)`` marks channel-scoped
    permissions. Everything else is server-scoped.
    """
    source = CATALOG_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = None
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SERVER_PERMISSIONS"
            for t in stmt.targets
        ):
            node = stmt
            break
    if node is None:
        raise AssertionError("SERVER_PERMISSIONS catalog not found")

    lines = source.splitlines()
    section = ""
    scopes = {}
    for line_no in range(node.lineno - 1, node.end_lineno):
        line = lines[line_no]
        stripped = line.strip()
        if stripped.startswith("#"):
            section = stripped.lstrip("# ").strip()
            continue
        match = _CATALOG_KEY_RE.match(line)
        if match:
            scopes[match.group(1)] = (
                _CHANNEL if CHANNEL_SECTION_MARKER in section.lower() else _SERVER
            )
    if not scopes:
        raise AssertionError("catalog derivation is vacuous: no permissions parsed")
    return scopes


def _walk_with_ancestors(root):
    """Yield ``(node, ancestors)`` in preorder with accurate ancestor chains."""

    def rec(node, ancestors):
        yield node, ancestors
        for child in ast.iter_child_nodes(node):
            yield from rec(child, ancestors + [node])

    return rec(root, [])


def _call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _resolve_permissions(call, ancestors, scopes):
    """Resolve a call's permission to catalog literals (or an empty list).

    Literal argument, ``permission=`` keyword, and ``for name in ("a", "b")``
    enum literals are supported. Anything that cannot be tied to a catalog key
    is skipped so wrappers/forwarders never produce findings.
    """
    candidates = []
    if len(call.args) >= 3:
        candidates.append(call.args[2])
    for kw in call.keywords:
        if kw.arg == "permission":
            candidates.append(kw.value)

    perms = []
    for cand in candidates:
        if isinstance(cand, ast.Constant) and isinstance(cand.value, str):
            perms.append(cand.value)
        elif isinstance(cand, ast.Name):
            for ancestor in ancestors:
                if (
                    isinstance(ancestor, (ast.For, ast.AsyncFor))
                    and isinstance(ancestor.target, ast.Name)
                    and ancestor.target.id == cand.id
                    and isinstance(ancestor.iter, (ast.Tuple, ast.List))
                ):
                    for element in ancestor.iter.elts:
                        if isinstance(element, ast.Constant) and isinstance(
                            element.value, str
                        ):
                            perms.append(element.value)
    return list(dict.fromkeys(p for p in perms if p in scopes))


def _call_scope(call):
    """Channel scope iff a channel argument is present; otherwise server."""
    if len(call.args) >= 4:
        return _CHANNEL
    if any(kw.arg in CHANNEL_KWARGS for kw in call.keywords):
        return _CHANNEL
    return _SERVER


def _analyze_source(source, path, scopes=None):
    """Analyse one source string; return (findings, stats).

    ``path`` is the repository-relative path used as the finding identity.
    The same function is used for the real tree and for every synthetic
    self-test, so the self-tests genuinely exercise the production logic.
    """
    scopes = scopes if scopes is not None else catalog_scopes()
    tree = ast.parse(textwrap.dedent(source))
    findings = []
    stats = Stats(parsed_files=1)
    for node, ancestors in _walk_with_ancestors(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name is None:
            continue
        is_manage = name.startswith(MANAGE_PREFIX)
        if name not in CHECK_NAMES and not is_manage:
            continue
        if is_manage:
            stats.manage_calls += 1
        else:
            stats.permission_checks += 1

        perms = _resolve_permissions(node, ancestors, scopes)
        if not perms:
            continue  # wrapper/forwarder -- not a scope-bearing gate
        scope = _call_scope(node)
        funcs = [
            a
            for a in ancestors
            if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        function = funcs[-1].name if funcs else "<module>"
        for perm in perms:
            intended = scopes[perm]
            if scope != intended:
                findings.append(
                    Finding(path, function, name, perm, scope, intended)
                )
    return findings, stats


def _scan_tree():
    """Analyse all of src/core and src/api; return (findings, stats)."""
    scopes = catalog_scopes()
    findings = []
    total = Stats()
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(SRC))
            file_findings, stats = _analyze_source(
                path.read_text(encoding="utf-8"), rel, scopes
            )
            findings.extend(file_findings)
            total.permission_checks += stats.permission_checks
            total.manage_calls += stats.manage_calls
            total.parsed_files += stats.parsed_files
    return findings, total


def _is_documented(finding):
    reason = DOCUMENTED_MISMATCHES.get(finding.key)
    return bool(reason and reason.strip())


# ---------------------------------------------------------------------------
# Real-tree invariants
# ---------------------------------------------------------------------------


def test_catalog_derives_scope_from_its_definitions():
    scopes = catalog_scopes()
    # The catalog's own scope marker must still be present, or every
    # permission would silently be classified as server-scoped.
    block = CATALOG_PATH.read_text(encoding="utf-8")
    assert CHANNEL_SECTION_MARKER in block
    assert scopes["channels.manage"] == _SERVER
    assert scopes["administrator"] == _SERVER
    assert scopes["messages.send"] == _CHANNEL
    assert scopes["messages.manage"] == _CHANNEL
    channel = {p for p, s in scopes.items() if s == _CHANNEL}
    server = {p for p, s in scopes.items() if s == _SERVER}
    assert channel and server, "scope derivation must be non-vacuous both ways"
    assert len(scopes) >= 40
    assert all(s in (_SERVER, _CHANNEL) for s in scopes.values())


def test_permission_scope_guard_has_no_unexpected_mismatch():
    findings, stats = _scan_tree()
    # Non-vacuity of enumeration: this is a real, broad sweep -- not a scan
    # that finds nothing because it parses nothing or resolves no permissions.
    assert stats.parsed_files > 200
    assert stats.permission_checks > 100
    assert stats.manage_calls > 0
    assert findings, "the analyser must actually detect known scope mismatches"

    unexpected = [f for f in findings if not _is_documented(f)]
    assert not unexpected, "new permission/scope mismatch:\n" + "\n".join(
        f.describe() for f in unexpected
    )


def test_documented_exceptions_are_precise_and_reasoned():
    for key, reason in DOCUMENTED_MISMATCHES.items():
        assert len(key) == 5
        path, function, call, permission, scope = key
        assert path.startswith(("core/", "api/"))
        assert function and function != "<module>"
        assert call in CHECK_NAMES or call.startswith(MANAGE_PREFIX)
        assert permission and "." in permission or permission == "administrator"
        assert scope in (_SERVER, _CHANNEL)
        assert isinstance(reason, str) and len(reason.strip()) >= 30


def test_every_documented_exception_currently_matches_a_finding():
    """Stale allowlist entries rot the guard; require each to be live."""
    findings, _ = _scan_tree()
    live = {f.key for f in findings}
    documented = set(DOCUMENTED_MISMATCHES)
    assert live == documented, (
        "documented entries drifted from real findings\n"
        f"  removed/fixed: {sorted(documented - live)}\n"
        f"  new undocumented: {sorted(live - documented)}"
    )


# ---------------------------------------------------------------------------
# Non-vacuity self-tests -- synthetic source through the same analyser
# ---------------------------------------------------------------------------

_NOVEL_PATH = "core/new_call_site/never_allowlisted.py"


def test_selftest_flags_server_permission_passed_channel_scope():
    """The exact 72d3f648 bug at a novel call site must be flagged."""
    source = '''
    def delete_channel(servers, user, server_id, channel_id):
        servers.require_permission(user, server_id, "channels.manage", channel_id)
    '''
    findings, stats = _analyze_source(source, _NOVEL_PATH)
    assert stats.permission_checks == 1
    assert len(findings) == 1
    f = findings[0]
    assert (f.path, f.function, f.call, f.permission, f.scope) == (
        _NOVEL_PATH,
        "delete_channel",
        "require_permission",
        "channels.manage",
        _CHANNEL,
    )
    assert f.intended == _SERVER
    assert not _is_documented(f), "a novel mismatched site must fail closed"


def test_selftest_flags_channel_permission_passed_server_scope():
    source = '''
    def gather(servers, user, server_id):
        return servers.has_permission(user, server_id, "messages.send")
    '''
    findings, _ = _analyze_source(source, _NOVEL_PATH)
    assert len(findings) == 1
    assert findings[0].permission == "messages.send"
    assert findings[0].scope == _SERVER
    assert findings[0].intended == _CHANNEL


def test_selftest_accepts_correct_scopes():
    source = '''
    def ok(servers, user, server_id, channel_id):
        servers.require_permission(user, server_id, "channels.manage")
        servers.has_permission(user, server_id, "messages.send", channel_id)
        servers.has_permission(user, server_id, "server.manage")
    '''
    findings, stats = _analyze_source(source, _NOVEL_PATH)
    assert stats.permission_checks == 3
    assert findings == []


def test_selftest_resolves_loop_variable_permission():
    """Covers the slowmode loop whose ``cid`` argument was removed by the fix."""
    source = '''
    async def set_channel_slowmode(servers, user, server_id, cid):
        for permission in ("channels.manage", "administrator"):
            if servers.has_permission(user, server_id, permission, cid):
                return True
        return False
    '''
    findings, _ = _analyze_source(source, _NOVEL_PATH)
    assert {f.permission for f in findings} == {"channels.manage", "administrator"}
    assert all(f.scope == _CHANNEL and f.intended == _SERVER for f in findings)
    assert all(not _is_documented(f) for f in findings)


def test_selftest_channel_keyword_argument_is_detected():
    source = '''
    def gate(servers, user, server_id, cid):
        servers.has_permission(user, server_id, "channels.manage", channel_id=cid)
    '''
    findings, _ = _analyze_source(source, _NOVEL_PATH)
    assert len(findings) == 1
    assert findings[0].scope == _CHANNEL
    assert not _is_documented(findings[0])


def test_selftest_same_finding_is_accepted_only_when_documented():
    """The allowlist is keyed by path/function: move the same bug and it fails."""
    source = '''
    def create_invite(servers, user, server_id, channel_id):
        return servers.require_permission(user, server_id, "invites.create", channel_id)
    '''
    documented_path = "core/servers/handlers/members/invites.py"
    findings, _ = _analyze_source(source, documented_path)
    assert len(findings) == 1
    assert _is_documented(findings[0]), "documented case must be accepted"

    moved, _ = _analyze_source(source, _NOVEL_PATH)
    assert len(moved) == 1
    assert not _is_documented(moved[0]), "same bug at a novel site must fail closed"


def test_selftest_fixed_roles_manage_site_is_no_longer_allowlisted():
    """A reintroduced channel-scoped roles.manage check must fail closed.

    The mention-validation site now resolves ``roles.manage`` at server scope.
    The old channel-scope form used to be documented as a deferred follow-up;
    that entry was removed with the fix, so reintroducing the bug at the same
    path must now be flagged rather than silently allowed.
    """
    source = '''
    def validate_mentions(servers, user, server_id, channel_id):
        return servers.has_permission(user, server_id, "roles.manage", channel_id)
    '''
    findings, _ = _analyze_source(
        source, "core/notifications/manager/mention_validator.py"
    )
    assert len(findings) == 1
    assert findings[0].permission == "roles.manage"
    assert findings[0].scope == _CHANNEL and findings[0].intended == _SERVER
    assert not _is_documented(findings[0]), "fixed site must no longer be allowlisted"
