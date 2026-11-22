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
   LOCAL ALIASES are resolved as well: check-shaped wrappers that forward their
   ``permission`` argument to a canonical gate (transitively) and plain aliases
   (``check = has_permission``) are mapped to their canonical check and their
   own call-site argument layout. Check-shaped names that cannot be resolved
   are still scanned as canonical checks (fail closed).

3. MISMATCH + ALLOWLIST -- a check whose scope differs from its catalog scope
   is a finding, unless its exact ``(path, function, call, permission, scope)``
   key is in :data:`DOCUMENTED_MISMATCHES` with a written reason. The key
   includes the path and function, so a NEW mismatch at a novel call site is
   never silently allowed: it fails closed.

4. NON-VACUITY -- the real-tree scan must enumerate a substantial number of
   checks, and parametrized self-tests feed synthetic source through the very
   same analyser to prove it flags the canonical bug, the reverse mismatch, the
   loop variable form, and that it accepts correct scope and documented cases.

5. PERMISSION-DICT GATES -- ``get_permissions(user, server[, channel])``
   returns a permission dict that call sites gate with ``[perm]`` or
   ``.get(perm)``, often via an assigned local (exactly the form N662 used).
   Those dict gates are invisible to the ``has_permission``-based scan above,
   which is how ``messages.export_transcript`` was resolved at channel scope
   unnoticed. They are analysed separately: the permission key is resolved
   from a literal (or a loop enum) and the catalog supplies its scope. A
   server-scoped permission resolved with a ``channel_id``/``cid`` is a
   finding, unless the (permission, scope) pair is allowlisted with a written
   reason (see :data:`DICT_GATE_SCOPE_ALLOWLIST`).

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

# The complete set of permissions the catalog legitimately scopes per channel
# (the ``# Message permissions (per channel)`` family). Pinned here so catalog
# drift -- a new permission family added under that comment, or a server-level
# / destructive permission reordered into that section -- fails the build
# instead of silently reclassifying a destructive check as channel-scoped.
# ``messages.export_transcript`` is deliberately absent: it is server-scoped
# ("Transcript export"), even though it shares the ``messages.`` prefix.
EXPECTED_CHANNEL_SCOPED = frozenset(
    {
        "messages.send",
        "messages.read",
        "messages.manage",
        "messages.embed_links",
        "messages.attach_files",
        "messages.mention_everyone",
        "messages.add_reactions",
        "messages.use_external_emojis",
        "messages.bypass_slowmode",
    }
)

# Permission/scope pairs that are legitimately produced by a *dict* gate on a
# channel-scoped ``get_permissions`` result. The catalog groups voice rights
# under a plain ``# Voice permissions`` section (no ``(per channel)`` marker),
# so a bare catalog lookup would classify them as server-scoped and flag the
# entirely correct ``get_permissions(user, server, cid)["voice.connect"]``
# pattern. Voice participation is a per-channel right enforced against that
# channel's overrides, so it is documented here. The ``messages.*`` entries are
# already channel-scoped in the catalog (no finding on their own); they are
# listed for completeness so the channel-scoped message/voice set is explicit.
DICT_GATE_SCOPE_ALLOWLIST = {
    ("voice.connect", _CHANNEL): (
        "Joining a voice channel is a per-channel participation right resolved "
        "against that channel's overrides; the catalog groups voice rights "
        "without a '(per channel)' marker, but channel scope is required."
    ),
    ("voice.speak", _CHANNEL): (
        "Speaking/streaming is a per-voice-channel participation right "
        "evaluated against that channel's overrides."
    ),
    ("voice.mute_members", _CHANNEL): (
        "Moderating members in a voice channel is bounded to that channel's "
        "overrides; channel scope is intentional."
    ),
    ("voice.deafen_members", _CHANNEL): (
        "Deafening members in a voice channel is bounded to that channel's "
        "overrides; channel scope is intentional."
    ),
    ("voice.move_members", _CHANNEL): (
        "Moving members between voice channels is evaluated against the "
        "source channel's overrides; channel scope is intentional."
    ),
    ("messages.send", _CHANNEL): (
        "Sending is a per-channel message right (catalog '(per channel)' "
        "section); a channel-scoped dict gate is the correct scope."
    ),
    ("messages.read", _CHANNEL): (
        "Reading is a per-channel message right; channel scope is correct."
    ),
    ("messages.manage", _CHANNEL): (
        "Deleting others' messages is a per-channel message right; channel "
        "scope is correct."
    ),
    ("messages.embed_links", _CHANNEL): (
        "Embedding links is a per-channel message right; channel scope is correct."
    ),
    ("messages.attach_files", _CHANNEL): (
        "Attaching files is a per-channel message right; channel scope is correct."
    ),
    ("messages.mention_everyone", _CHANNEL): (
        "Mentions are a per-channel message right; channel scope is correct."
    ),
    ("messages.add_reactions", _CHANNEL): (
        "Reactions are a per-channel message right; channel scope is correct."
    ),
    ("messages.use_external_emojis", _CHANNEL): (
        "External emoji use is a per-channel message right; channel scope is correct."
    ),
    ("messages.bypass_slowmode", _CHANNEL): (
        "Slowmode bypass is a per-channel message right; channel scope is correct."
    ),
}


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
    dict_gates: int = 0
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
    (
        "core/threads/manager/permissions.py",
        "_can_view_thread_row",
        "_check_permission",
        "channels.view",
        _CHANNEL,
    ): (
        "Public-thread visibility mirrors can_view_thread: channels.view is "
        "evaluated for the thread's backing channel so a caller without view "
        "cannot see the thread. Non-authoritative visibility check, the same "
        "per-channel pattern as the other channels.view entries."
    ),
    (
        "core/threads/manager/permissions.py",
        "can_manage_thread",
        "_check_permission",
        "threads.manage",
        _CHANNEL,
    ): (
        "Thread management is channel-bounded: archive/lock/rename/delete and "
        "roster changes affect one thread in the granted channel, gated further "
        "by _can_view_thread_row and private-thread membership. A channel-only "
        "grant cannot perform a server-level destructive write (unlike the "
        "72d3f648 channels.manage case). The catalog's coarse section comment "
        "marks threads.manage server-scoped; here channel scope is intentional."
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


# A local wrapper/alias is any name that reads like a permission check. We only
# treat it as a check if we can prove it delegates to a canonical gate (or, for
# unresolvable names, fail closed -- see ``_analyze_source``). The name filter
# keeps unrelated helpers (``get_my_permissions``, ``validate_permissions``,
# ``grant_permission`` ...) out of the alias set.
CHECK_ALIAS_RE = re.compile(
    r"(check|require|has|verify|assert|ensure)[_]?perm|perm(ission)?[_]?(check|require)",
    re.IGNORECASE,
)


def _looks_like_check(name):
    return bool(name and CHECK_ALIAS_RE.search(name))


@dataclass(frozen=True)
class CheckLayout:
    """Where permission/channel arguments live at a check's call site."""

    perm_index: int = 2
    perm_kwarg: str = "permission"
    channel_index: int = 3
    channel_kwargs: frozenset = CHANNEL_KWARGS


CANONICAL_LAYOUT = CheckLayout()

# ``get_permissions(user, server, channel_id=None)`` carries the channel at
# positional index 2 (not 3 like the check functions). Its result is a dict
# that call sites gate directly, so only the channel argument matters here.
GET_PERMISSIONS_LAYOUT = CheckLayout(
    perm_index=None, perm_kwarg=None, channel_index=2
)


def _arg_at(call, index, kwarg=None, kwargs=frozenset()):
    """Return the expression passed in a given argument slot, or None."""
    if index is not None and len(call.args) > index:
        return call.args[index]
    for kw in call.keywords:
        if (kwarg and kw.arg == kwarg) or kw.arg in kwargs:
            return kw.value
    return None


def _wrapper_params(fn):
    """Wrapper parameters as seen at a call site (a bound ``self``/``cls`` is not passed)."""
    params = [a.arg for a in list(fn.args.posonlyargs) + list(fn.args.args)]
    if params and params[0] in ("self", "cls"):
        params = params[1:]
    return params


def _resolve_wrapper(fn, aliases):
    """Resolve a permission-check wrapper to ``(canonical, call-site layout)``.

    Only wrappers that FORWARD their permission argument are resolved: when a
    wrapper hardcodes the permission (e.g. ``_check_embed_links_permission``),
    its own internal canonical call already carries the scope and there is
    nothing to scan at its call sites. Delegation is followed transitively
    through other already-resolved aliases (bounded by the caller's passes).
    """
    params = _wrapper_params(fn)
    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Call):
            continue
        name = _call_name(sub)
        if name in CHECK_NAMES:
            canonical, inner = name, CANONICAL_LAYOUT
        elif name in aliases:
            canonical, inner = aliases[name]
        else:
            continue
        perm_expr = _arg_at(sub, inner.perm_index, inner.perm_kwarg)
        if not isinstance(perm_expr, ast.Name) or perm_expr.id not in params:
            continue
        channel_expr = _arg_at(
            sub, inner.channel_index, None, inner.channel_kwargs
        )
        channel_param = (
            channel_expr.id
            if isinstance(channel_expr, ast.Name) and channel_expr.id in params
            else None
        )
        return (
            canonical,
            CheckLayout(
                perm_index=params.index(perm_expr.id),
                perm_kwarg=perm_expr.id,
                channel_index=params.index(channel_param) if channel_param else None,
                channel_kwargs=frozenset({channel_param}) if channel_param else frozenset(),
            ),
        )
    return None


def _resolve_alias_definitions(tree):
    """Return ``(aliases, unresolved_names)`` for one parsed source tree.

    ``aliases`` maps a local check name to ``(canonical, layout)``. Support both
    wrappers (``def _check_permission(...): return ...has_permission(...)``) and
    plain module-level aliases (``check = has_permission``).
    """
    functions = {}
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _looks_like_check(node.name):
                functions[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                # The RHS proves this is an alias regardless of the target's
                # name (e.g. ``check = has_permission``), so no name filter here.
                if not isinstance(target, ast.Name):
                    continue
                rhs = node.value
                if isinstance(rhs, ast.Name) and rhs.id in CHECK_NAMES:
                    aliases[target.id] = (rhs.id, CANONICAL_LAYOUT)
                elif isinstance(rhs, ast.Attribute) and rhs.attr in CHECK_NAMES:
                    aliases[target.id] = (rhs.attr, CANONICAL_LAYOUT)

    # Bounded transitive resolution (a wrapper may delegate via another alias).
    for _ in range(8):
        changed = False
        for name, fn in functions.items():
            if name in aliases:
                continue
            resolved = _resolve_wrapper(fn, aliases)
            if resolved:
                aliases[name] = resolved
                changed = True
        if not changed:
            break
    return aliases, set(functions) - set(aliases)


def _perm_literals(expr, ancestors, scopes):
    """Resolve one expression to catalog permission keys (or an empty list).

    Literal strings and ``for name in ("a", "b")`` enum literals are supported.
    Anything that cannot be tied to a catalog key is skipped so wrappers /
    forwarders never produce findings. Shared by the call-based scan and the
    permission-dict-gate scan below.
    """
    perms = []
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        perms.append(expr.value)
    elif isinstance(expr, ast.Name):
        for ancestor in ancestors:
            if (
                isinstance(ancestor, (ast.For, ast.AsyncFor))
                and isinstance(ancestor.target, ast.Name)
                and ancestor.target.id == expr.id
                and isinstance(ancestor.iter, (ast.Tuple, ast.List))
            ):
                for element in ancestor.iter.elts:
                    if isinstance(element, ast.Constant) and isinstance(
                        element.value, str
                    ):
                        perms.append(element.value)
    return list(dict.fromkeys(p for p in perms if p in scopes))


def _resolve_permissions(call, ancestors, scopes, layout=CANONICAL_LAYOUT):
    """Resolve a call's permission to catalog literals (or an empty list).

    Literal argument, ``permission=`` keyword, and ``for name in ("a", "b")``
    enum literals are supported. Anything that cannot be tied to a catalog key
    is skipped so wrappers/forwarders never produce findings.
    """
    candidates = []
    perm_expr = _arg_at(call, layout.perm_index, layout.perm_kwarg)
    if perm_expr is not None:
        candidates.append(perm_expr)
    # A ``permission=`` keyword is accepted regardless of layout: wrappers that
    # forward by name keep the canonical keyword even when their positional
    # order differs.
    for kw in call.keywords:
        if kw.arg == "permission" and kw.value is not perm_expr:
            candidates.append(kw.value)

    perms = []
    for cand in candidates:
        perms.extend(_perm_literals(cand, ancestors, scopes))
    return list(dict.fromkeys(perms))


def _call_scope(call, layout=CANONICAL_LAYOUT):
    """Channel scope iff a channel argument is present; otherwise server."""
    if layout.channel_index is not None and len(call.args) > layout.channel_index:
        return _CHANNEL
    if any(kw.arg in layout.channel_kwargs for kw in call.keywords):
        return _CHANNEL
    return _SERVER


def _analyze_permission_dict_gates(
    tree, walked, ancestors_of, path, scopes, findings, stats
):
    """Analyse dict gates applied to ``get_permissions(...)`` results.

    ``get_permissions(user, server[, channel])`` returns a permission dict that
    call sites gate with ``[perm]`` / ``.get(perm)``. N662 fixed one such gate
    that was resolved at channel scope, but this form was invisible to the
    ``has_permission``-based scan. For every gate we resolve the permission key
    (literal or loop enum) and the scope of the originating ``get_permissions``
    call; a server-scoped permission resolved with a channel argument (or the
    reverse) is a finding unless documented/allowlisted.

    Both the direct form (``get_permissions(...)[perm]``) and the assigned-local
    form (``perms = get_permissions(...) or {}; perms.get(perm)``) are handled,
    as is the ``or {}`` wrapper. All self-test inputs flow through this same
    function, so they exercise the production logic.
    """
    seen = set()

    for gp, gp_anc in walked:
        if not isinstance(gp, ast.Call) or _call_name(gp) != "get_permissions":
            continue
        scope = _call_scope(gp, GET_PERMISSIONS_LAYOUT)
        funcs = [
            a for a in gp_anc if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        function = funcs[-1].name if funcs else "<module>"
        scope_node = funcs[-1] if funcs else tree

        # Walk through an ``or {}`` (or similar) wrapper to find the actual
        # gate expression / assignment that consumes this result.
        wrapper = gp
        parent = gp_anc[-1] if gp_anc else None
        while isinstance(parent, ast.BoolOp):
            wrapper = parent
            w_anc = ancestors_of[id(parent)]
            parent = w_anc[-1] if w_anc else None

        gate_exprs = []  # (permission expression, its ancestor chain)

        # Direct: ``get_permissions(...)[perm]`` / ``get_permissions(...).get(perm)``.
        if isinstance(parent, ast.Subscript) and parent.value is wrapper:
            gate_exprs.append((parent.slice, ancestors_of[id(parent)]))
        # ``get_permissions(...).get(perm)``: the call is the *value* of the
        # ``.get`` attribute, whose parent is the outer call.
        if (
            isinstance(parent, ast.Attribute)
            and parent.attr == "get"
            and parent.value is wrapper
        ):
            p_anc = ancestors_of[id(parent)]
            grandparent = p_anc[-1] if p_anc else None
            if (
                isinstance(grandparent, ast.Call)
                and grandparent.func is parent
                and grandparent.args
            ):
                gate_exprs.append((grandparent.args[0], ancestors_of[id(grandparent)]))
        # ``(get_permissions(...) or {}).get(perm)`` handled above via wrapper,
        # plus the already-unwrapped ``Call(func=Attribute(get))`` parent form.
        if (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Attribute)
            and parent.func.attr == "get"
            and parent.func.value is wrapper
            and parent.args
        ):
            gate_exprs.append((parent.args[0], ancestors_of[id(parent)]))

        # Assigned local: ``perms = get_permissions(...)[or {}]; perms[perm]``.
        target = None
        if isinstance(parent, ast.Assign) and len(parent.targets) == 1:
            if isinstance(parent.targets[0], ast.Name):
                target = parent.targets[0].id
        elif isinstance(parent, ast.NamedExpr) and isinstance(parent.target, ast.Name):
            target = parent.target.id
        if target:
            for use in ast.walk(scope_node):
                if (
                    isinstance(use, ast.Call)
                    and isinstance(use.func, ast.Attribute)
                    and use.func.attr == "get"
                    and isinstance(use.func.value, ast.Name)
                    and use.func.value.id == target
                    and use.args
                ):
                    gate_exprs.append((use.args[0], ancestors_of[id(use)]))
                if (
                    isinstance(use, ast.Subscript)
                    and isinstance(use.value, ast.Name)
                    and use.value.id == target
                ):
                    gate_exprs.append((use.slice, ancestors_of[id(use)]))

        for perm_expr, gate_anc in gate_exprs:
            for perm in _perm_literals(perm_expr, gate_anc, scopes):
                if (id(gp), perm) in seen:
                    continue
                seen.add((id(gp), perm))
                stats.dict_gates += 1
                intended = scopes[perm]
                if scope == intended:
                    continue
                if (perm, scope) in DICT_GATE_SCOPE_ALLOWLIST:
                    continue
                finding = Finding(
                    path, function, "get_permissions", perm, scope, intended
                )
                if not _is_documented(finding):
                    findings.append(finding)


def _analyze_source(source, path, scopes=None):
    """Analyse one source string; return (findings, stats).

    ``path`` is the repository-relative path used as the finding identity.
    The same function is used for the real tree and for every synthetic
    self-test, so the self-tests genuinely exercise the production logic.
    Local aliases/wrappers are resolved against the same source tree, so their
    call sites are scanned with the wrapper's own argument layout.
    """
    scopes = scopes if scopes is not None else catalog_scopes()
    tree = ast.parse(textwrap.dedent(source))
    aliases, unresolved = _resolve_alias_definitions(tree)
    findings = []
    stats = Stats(parsed_files=1)
    walked = list(_walk_with_ancestors(tree))
    ancestors_of = {id(node): ancestors for node, ancestors in walked}
    for node, ancestors in walked:
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name is None:
            continue

        is_manage = name.startswith(MANAGE_PREFIX)
        if name in CHECK_NAMES:
            layout = CANONICAL_LAYOUT
        elif name in aliases:
            layout = aliases[name][1]  # canonical + call-site layout
        elif is_manage:
            layout = CANONICAL_LAYOUT
        elif name in unresolved:
            # Fail closed: an unresolvable permission-check-shaped call is
            # treated like a canonical check. If its arguments carry a catalog
            # permission and a channel scope that mismatches, flag it for
            # review rather than silently dropping it.
            layout = CANONICAL_LAYOUT
        else:
            continue
        if is_manage:
            stats.manage_calls += 1
        else:
            stats.permission_checks += 1

        perms = _resolve_permissions(node, ancestors, scopes, layout)
        if not perms:
            continue  # wrapper/forwarder -- not a scope-bearing gate
        scope = _call_scope(node, layout)
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

    _analyze_permission_dict_gates(
        tree, walked, ancestors_of, path, scopes, findings, stats
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
            try:
                source_text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                # A concurrent writer may atomically replace a file between the
                # glob and the read; skip it rather than fail the whole guard.
                continue
            file_findings, stats = _analyze_source(source_text, rel, scopes)
            findings.extend(file_findings)
            total.permission_checks += stats.permission_checks
            total.manage_calls += stats.manage_calls
            total.dict_gates += stats.dict_gates
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

    # Fail closed on catalog drift. ``channel`` is derived purely from the
    # nearest preceding ``(per channel)`` section comment, so a permission
    # family inserted under that comment -- or an existing server-level /
    # destructive permission reordered into that section -- would otherwise be
    # classified as channel-scoped. A channel-scoped check of such a permission
    # would then produce NO finding and silently authorise the 72d3f648-style
    # escalation. Pin the exact set so any drift fails the build and forces a
    # reviewed decision.
    assert channel == set(EXPECTED_CHANNEL_SCOPED), (
        "catalog-derived channel scope set drifted -- a permission was added "
        "under or moved into a '(per channel)' section, or removed from it\n"
        f"  unexpected channel-scoped: {sorted(channel - set(EXPECTED_CHANNEL_SCOPED))}\n"
        f"  missing:                   {sorted(set(EXPECTED_CHANNEL_SCOPED) - channel)}"
    )


def test_permission_scope_guard_has_no_unexpected_mismatch():
    findings, stats = _scan_tree()
    # Non-vacuity of enumeration: this is a real, broad sweep -- not a scan
    # that finds nothing because it parses nothing or resolves no permissions.
    assert stats.parsed_files > 200
    assert stats.permission_checks > 100
    assert stats.manage_calls > 0
    assert stats.dict_gates > 0, "permission-dict-gate scan must not be vacuous"
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
        assert (
            call in CHECK_NAMES
            or call.startswith(MANAGE_PREFIX)
            or _looks_like_check(call)
        )
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


def test_selftest_mislabeled_destructive_permission_is_invisible_to_analyser():
    """Prove the catalog pin is non-vacuous.

    Scope is derived solely from the ``(per channel)`` catalog comment. If a
    destructive server-level permission were misclassified as channel-scoped
    (e.g. inserted under that comment), the analyser would consider a
    channel-scoped check of it CORRECT and emit no finding at all -- the
    72d3f648 escalation, invisible to the mismatch scan. The exact-set
    assertion in ``test_catalog_derives_scope_from_its_definitions`` is what
    fails the build in that case; this test demonstrates why it is required.
    """
    destructive = "server.wipe_members"
    assert destructive not in EXPECTED_CHANNEL_SCOPED
    assert destructive not in catalog_scopes()  # not currently in the catalog

    # Simulate the misclassification the catalog pin guards against.
    scopes = dict(catalog_scopes())
    scopes[destructive] = _CHANNEL
    source = '''
    def wipe(servers, user, server_id, channel_id):
        servers.require_permission(user, server_id, "server.wipe_members", channel_id)
    '''
    findings, _ = _analyze_source(source, _NOVEL_PATH, scopes=scopes)
    assert findings == [], "a mislabeled destructive perm evades the mismatch scan"

    # And the pinned-set check would reject that catalog state loudly.
    channel = {p for p, s in scopes.items() if s == _CHANNEL}
    assert channel != set(EXPECTED_CHANNEL_SCOPED)
    assert destructive in channel - set(EXPECTED_CHANNEL_SCOPED)


# ---------------------------------------------------------------------------
# Alias / wrapper resolution self-tests
# ---------------------------------------------------------------------------

_ALIAS_WRAPPER = '''
    class Gate:
        def _check_permission(self, user, server, permission, channel_id=None):
            return self._servers.has_permission(user, server, permission, channel_id)

        def bad(self, user, server, cid):
            return self._check_permission(user, server, "channels.manage", cid)

        def ok(self, user, server, cid):
            self._check_permission(user, server, "messages.send", cid)
            self._check_permission(user, server, "channels.manage")
'''


def test_selftest_alias_mismatch_is_flagged():
    """A server-scoped permission checked via a local wrapper fails closed."""
    aliases, unresolved = _resolve_alias_definitions(ast.parse(textwrap.dedent(_ALIAS_WRAPPER)))
    assert "_check_permission" in aliases, "wrapper must be resolved to canonical"
    assert "_check_permission" not in unresolved

    findings, stats = _analyze_source(_ALIAS_WRAPPER, _NOVEL_PATH)
    assert stats.permission_checks >= 3  # two call sites in ok/bad + wrapper forward
    bad = [f for f in findings if f.function == "bad"]
    assert len(bad) == 1
    f = bad[0]
    assert (f.call, f.permission, f.scope, f.intended) == (
        "_check_permission",
        "channels.manage",
        _CHANNEL,
        _SERVER,
    )
    assert not _is_documented(f), "a novel alias mismatch must fail closed"
    assert not [f for f in findings if f.function == "ok"], "correct alias scopes accepted"


def test_selftest_assignment_alias_is_scanned():
    """``check = has_permission`` aliases are resolved from the RHS."""
    source = '''
    check = has_permission

    def gate(user, server, cid):
        check(user, server, "channels.manage", cid)
    '''
    aliases, _ = _resolve_alias_definitions(ast.parse(textwrap.dedent(source)))
    assert aliases["check"][0] == "has_permission"
    findings, _ = _analyze_source(source, _NOVEL_PATH)
    assert len(findings) == 1
    assert (findings[0].call, findings[0].permission, findings[0].scope) == (
        "check",
        "channels.manage",
        _CHANNEL,
    )


def test_selftest_unresolvable_check_alias_fails_closed():
    """A check-shaped alias we cannot resolve is still scanned, not dropped."""
    source = '''
    def _check_permission(user, server, permission, channel_id=None):
        return external_policy.allow(user, server, permission, channel_id)

    def gate(user, server, cid):
        return _check_permission(user, server, "channels.manage", cid)
    '''
    aliases, unresolved = _resolve_alias_definitions(ast.parse(textwrap.dedent(source)))
    assert "_check_permission" not in aliases
    assert "_check_permission" in unresolved
    findings, _ = _analyze_source(source, _NOVEL_PATH)
    assert len(findings) == 1
    assert (findings[0].call, findings[0].permission, findings[0].scope) == (
        "_check_permission",
        "channels.manage",
        _CHANNEL,
    )


def test_alias_resolution_is_active_on_real_tree():
    """Alias findings must be live, or the allowlist entries go stale."""
    findings, _ = _scan_tree()
    alias_findings = [
        f for f in findings if f.call in {"_check_permission", "_require_permission"}
    ]
    assert alias_findings, "no alias checks enumerated -- resolution is vacuous"
    assert all(_is_documented(f) for f in alias_findings)
    assert {f.permission for f in alias_findings} >= {"channels.view", "threads.manage"}


# ---------------------------------------------------------------------------
# Permission-dict-gate self-tests -- the N662 form through the same analyser
# ---------------------------------------------------------------------------


def test_selftest_dict_gate_flags_server_perm_with_channel_scope():
    """The exact N662 bug: assigned-local dict gate with a channel id."""
    source = '''
    def request_transcript_export(servers, user, server_id, cid):
        permissions = servers.get_permissions(user, server_id, cid) or {}
        if not permissions.get("messages.export_transcript"):
            return None
        if not permissions["administrator"]:
            return None
        return "exported"
    '''
    findings, stats = _analyze_source(source, _NOVEL_PATH)
    gate_findings = [f for f in findings if f.call == "get_permissions"]
    assert stats.dict_gates == 2
    assert {(f.permission, f.scope, f.intended) for f in gate_findings} == {
        ("messages.export_transcript", _CHANNEL, _SERVER),
        ("administrator", _CHANNEL, _SERVER),
    }
    assert all(f.function == "request_transcript_export" for f in gate_findings)
    assert all(not _is_documented(f) for f in gate_findings), (
        "a novel server-scoped dict gate at channel scope must fail closed"
    )


def test_selftest_dict_gate_direct_subscript_and_get_are_flagged():
    """Both direct gate forms are recognised, not just the assigned-local form."""
    source = '''
    def gate(servers, user, server_id, cid):
        assert servers.get_permissions(user, server_id, cid)["messages.export_transcript"]
        assert servers.get_permissions(user, server_id, cid).get("administrator")
    '''
    findings, stats = _analyze_source(source, _NOVEL_PATH)
    gate_findings = [f for f in findings if f.call == "get_permissions"]
    assert stats.dict_gates == 2
    assert {f.permission for f in gate_findings} == {
        "messages.export_transcript",
        "administrator",
    }
    assert all(f.scope == _CHANNEL and f.intended == _SERVER for f in gate_findings)


def test_selftest_dict_gate_accepts_correct_scopes():
    """Correct server scope and channel scope are both accepted."""
    source = '''
    def ok(servers, user, server_id, cid):
        server_perms = servers.get_permissions(user, server_id)
        assert server_perms["messages.export_transcript"]
        assert server_perms.get("administrator")
        channel_perms = servers.get_permissions(user, server_id, cid) or {}
        assert channel_perms["messages.send"]
        assert channel_perms.get("messages.bypass_slowmode")
    '''
    findings, stats = _analyze_source(source, _NOVEL_PATH)
    assert stats.dict_gates == 4
    assert [f for f in findings if f.call == "get_permissions"] == []


def test_selftest_dict_gate_allowlist_is_precise_and_non_vacuous():
    """Documented channel-scoped message/voice perms are allowed; others are not."""
    for (perm, scope), reason in DICT_GATE_SCOPE_ALLOWLIST.items():
        assert scope == _CHANNEL
        assert perm in catalog_scopes(), "allowlisted perm must be real"
        assert perm in EXPECTED_CHANNEL_SCOPED or perm.startswith("voice.")
        assert isinstance(reason, str) and len(reason.strip()) >= 30

    source = '''
    def join(servers, user, server_id, cid):
        perms = servers.get_permissions(user, server_id, cid) or {}
        assert perms.get("voice.connect")          # allowlisted voice perm
        assert perms["messages.send"]              # catalog channel-scoped
        assert not perms.get("messages.export_transcript")  # real finding
    '''
    findings, _ = _analyze_source(source, _NOVEL_PATH)
    gate_findings = [f for f in findings if f.call == "get_permissions"]
    assert len(gate_findings) == 1, "only the unallowlisted server perm is flagged"
    f = gate_findings[0]
    assert (f.permission, f.scope, f.intended) == (
        "messages.export_transcript",
        _CHANNEL,
        _SERVER,
    )
    # Removing the allowlist key would make the voice gate a finding too.
    assert ("voice.connect", _CHANNEL) in DICT_GATE_SCOPE_ALLOWLIST


def test_dict_gate_scan_is_active_on_real_tree():
    """Dict gates exist in the real tree, so the new scan is not vacuous."""
    _, stats = _scan_tree()
    assert stats.dict_gates >= 3
