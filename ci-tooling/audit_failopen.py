#!/usr/bin/env python3
"""Fail-open authorization audit (re-runnable).

The findings annexes (O/P/Q) were produced by hand and went stale: by the time
they were re-checked, most listed items had already been remediated, so the
annex text no longer described the tree. This tool replaces that prose with an
executable sweep so the inventory can be regenerated on demand and gated in CI.

It looks for the single invariant class that caused every one of those bugs:

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

Sweeps:

  A. ``src/core`` manager guards -- ``if not self._<dep>:`` whose block grants
     access (``return True``) instead of denying.
  B. ``src/api`` module guards -- ``if <module>:`` blocks that call an
     authorization helper (``has_permission``/``get_permissions``/
     ``require_permission``/``get_member``/``is_ip_blocked``) and have no
     denying branch. Verified non-authoritative guards are recorded in
     ``REVIEWED_GUARDS`` with a reason.
  C. ``plexichat-client`` -- ``canEdit``-style gates that fail open on an
     unknown viewer, or that decide access without consulting any
     permission/ownership/role data.
  D. ``src/api`` route + websocket-handler inventory -- the *missing check*
     class, where no guard exists to inspect at all.
  E. ``src/core`` manager write methods -- a mutating method that takes both a
     caller id (``user_id``/``admin_id``/...) and a resource id but never uses
     the caller to resolve access: the manager-layer mirror of D's
     ``UNAUTHZ``.
  F. ``src`` private-helper probes -- ``getattr(<facade>, "_helper", None)``
     where the helper is resolved off an object that may be a *module* facade
     or a *manager* instance, and the ``None`` branch bails out of an
     authorization guard instead of denying. This is the shape the voice
     fail-open took: the production wiring passes the ``src.core.voice``
     module, the private helpers live on the manager, so ``getattr`` returned
     the default and ``return`` skipped ``voice.connect`` entirely.
  G. ``src`` SDP answers -- a function that assembles an SDP answer and mints
     a fresh random value for an ICE credential or DTLS fingerprint the SFU
     never supplied. The same fail-open shape as A-F in the media plane: the
     missing dependency (real transport parameters) is replaced with a value
     that makes the negotiation *appear* to succeed, while no media can flow.
  H. ``plexichat-client`` <-> ``src/api`` media callbacks -- a field the
     client sends on a voice opcode that the matching gateway handler never
     reads. The callback is *recorded* (sent) and never *delivered* (consumed):
     the peer speaking indicator (``speaking``/``volume``) and the recording
     consent (``consented``) both sat in this class, the latter while
     ``VoiceCallManager.add_consent`` -- the only writer of
     ``consented_participants`` -- had no caller at all. Sweeps A-G ask whether
     a check runs; H asks whether the thing it guards ever arrives.
  I. ``src`` (core managers + API handlers) access decisions derived from a
     listing the function itself fetched: the check runs, but its input is a
     row of that listing, so the answer describes the data rather than the
     caller. The threads-listing bug fixed by the fail-open work
     (``ANNEX AB``) was exactly this -- the guard folded the fetched threads
     into a flag and the permission consumed the first thread's
     ``server_id``. A hit needs both halves (a guard over a collection *and*
     an access call consuming a row of it), so a resource lookup authorizing
     against the resource it fetched is not reported.

Sweep H needs both trees: it is skipped (and reported as skipped, never as a
pass) when the sibling ``plexichat-client`` checkout is absent. Fields that are
informational by design carry a recorded verdict in ``REVIEWED_CALLBACK_FIELDS``
/ ``REVIEWED_CALLBACK_FIELDS_BY_OPCODE`` -- an unreviewed hit gates the run.

Sweeps A-C find *fail-open guards*: an existing check that skips. Sweep D is a
presence check over every HTTP route and gateway opcode handler, parsed with
``ast`` (so multi-line signatures and the ``router.get(...)(handler)``
composer registration form are both handled):

  * ``NO-AUTH`` -- no authentication mechanism at all (neither a
    ``Depends(get_current_user)`` dependency, an admin host/permission gate,
    a federation HMAC check, nor a credential the handler itself verifies).
  * ``UNAUTHZ`` -- a *mutating* route that resolves the caller but never uses
    that identity and makes no visible permission/membership/scope call: a
    candidate missing authorization check (IDOR shape).
  * ``PUBLIC`` -- verified by design (pre-auth account lifecycle, credential
    exchange, public read-only resources, signed inbound traffic). Recorded in
    ``PUBLIC_FILES`` / ``PUBLIC_BY_DESIGN`` / ``REVIEWED_OK`` with a reason.

Absence of a signal is not proof of a bug -- authorization is often delegated
to the manager the handler calls -- so a run is only clean once every
remaining hit has been individually verified and recorded. The allowlists are
part of the audit: a new route that trips a sweep must be verified (and the
verdict recorded) before the run returns 0.

It optionally cross-checks ``findings.md`` so the printed inventory states how
many recorded findings are still open versus annotated FIXED.

Usage:
    python ci-tooling/audit_failopen.py [--findings ../findings.md] [--root .]

Exit code 1 when any fail-open class (GRANT, FABRICATED, EMPTY-DECIDED) or
unexplained NO-AUTH/UNCHECKED/UNDELIVERED hit remains, 0 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Classification vocabulary
# ---------------------------------------------------------------------------

GRANT = "GRANT"  # fails open: the guard skips the check when unwired -> bug
REVIEW = "REVIEW"  # needs human judgement (may be non-authoritative plumbing)
NON_AUTH = "NON-AUTH"  # bookkeeping / caching / logging, not an authz decision
UNCHECKED = "UNCHECKED"  # sweep E: write method takes a caller id it never uses
NO_AUTH = "NO-AUTH"  # sweep D: endpoint with no authentication dependency
UNAUTHZ = "UNAUTHZ"  # sweep D: authenticated but no visible authorization call
PUBLIC = "PUBLIC"  # sweep D: verified public/self-scoped by design
UNDELIVERED = "UNDELIVERED"  # sweep H: a field the client sends that no handler reads

# Cross-module dependencies: their absence means "cannot authorize".
DEP_ATTRS = {
    "_servers",
    "_servers_module",
    "_relationships",
    "_relationships_module",
    "_messaging",
    "_messaging_module",
    "_auth",
    "_auth_module",
    "_presence",
    "_presence_module",
    "_webhooks",
    "_threads",
    "_media",
    "_media_module",
    "_embeds",
    "_features",
    "_search",
    "_applications",
    "_notifications",
    "_stickers",
    "_soundboard",
    "_polls",
    "_settings",
    "_avatars",
    "_admin",
    "_telemetry",
    "_events",
    "_automod",
    "_moderation",
    "_ratelimit",
    "_valkey",
}

DEP_NAMES = {name.lstrip("_") for name in DEP_ATTRS} | {
    "servers_mod",
    "servers_module",
    "relationships_mod",
    "messaging_mod",
    "auth_mod",
    "presence_mod",
    "webhooks_mod",
    "threads_mod",
    "media_mod",
    "embeds_mod",
    "stickers_mod",
    "soundboard_mod",
    "polls_module",
    "settings_module",
    "applications_mod",
    "valkey",
    "redis",
}


def _is_dependency(symbol: str) -> bool:
    """True when the guarded name is a cross-module dependency.

    Local variables fetched from a query (``row``, ``stored``, ``file``,
    ``perms`` ...) are truthiness checks, not dependency guards, and must not
    be reported as fail-open authorization guards.
    """
    return symbol in DEP_ATTRS or symbol in DEP_NAMES


# Authorization-oriented calls; a guard block that skips one of these without a
# denying branch is a candidate.
AUTHZ_CALLS = (
    "has_permission",
    "get_permissions",
    "require_permission",
    "get_member",
    "is_ip_blocked",
    "is_participant",
    "can_view",
    "check_admin_permission",
)

DENY_TOKENS = ("raise ", "return False", "return None", "deny", "forbidden")

# Sweep B guards already inspected and confirmed non-authoritative: the
# guarded call is event fan-out / best-effort plumbing, never the access
# check itself (the mutation is gated by the manager the handler calls).
# Key: (relative path, guarded symbol). Value: why it is not a bug.
REVIEWED_GUARDS: dict[tuple[str, str], str] = {
    (
        "src/api/routes/messages/messages_crud/broadcast.py",
        "servers_mod",
    ): (
        "recipient fan-out only; the send mutation is authorized inside the "
        "messaging manager"
    ),
    ("src/api/routes/messages/pins.py", "servers_mod"): (
        "resolves the broadcast recipient set only; `messaging.pin_message` / "
        "`messaging.unpin_message` enforce the permission"
    ),
    ("src/api/routes/messages/pins.py", "messaging"): (
        "same fan-out path; the pin/unpin permission is enforced in the manager"
    ),
    ("src/api/routes/presence.py", "servers"): (
        "builds the online-presence recipient set; never an access decision"
    ),
    ("src/api/websocket/handlers/presence.py", "servers_mod"): (
        "typing-event recipient resolution; each recipient is permission-checked"
    ),
}

GUARD_INVERTED = re.compile(r"^\s*if not (self\.)?(_?[A-Za-z_][A-Za-z0-9_]*)\s*:\s*$")
GUARD_NONE = re.compile(r"^\s*if (self\.)?(_?[A-Za-z_][A-Za-z0-9_]*) is None\s*:\s*$")
GUARD_POSITIVE = re.compile(r"^\s*if (self\.)?(_?[A-Za-z_][A-Za-z0-9_]*)\s*:\s*$")


class Hit(NamedTuple):
    kind: str
    path: str
    line: int
    symbol: str
    detail: str


def _block_after(lines: list[str], idx: int) -> list[str]:
    """Return the indented block following ``lines[idx]``."""
    head = lines[idx]
    indent = len(head) - len(head.lstrip())
    block: list[str] = []
    for follow in lines[idx + 1 :]:
        if not follow.strip():
            block.append(follow)
            continue
        if len(follow) - len(follow.lstrip()) <= indent:
            break
        block.append(follow)
    return block


def _has_deny(block: Iterable[str]) -> bool:
    text = "\n".join(block)
    return any(token in text for token in DENY_TOKENS)


def _has_grant(block: Iterable[str]) -> bool:
    return any("return True" in line for line in block)


def sweep_core(root: Path) -> list[Hit]:
    """Sweep A: manager guards in ``src/core`` that fail open."""
    hits: list[Hit] = []
    for path in sorted((root / "src" / "core").rglob("*.py")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for idx, line in enumerate(lines):
            for pattern in (GUARD_INVERTED, GUARD_NONE):
                match = pattern.match(line)
                if not match:
                    continue
                symbol = match.group(2)
                if not _is_dependency(symbol):
                    continue
                block = _block_after(lines, idx)
                if not block:
                    continue
                kind = GRANT if _has_grant(block) and not _has_deny(block) else NON_AUTH
                if kind == GRANT:
                    hits.append(
                        Hit(
                            kind,
                            str(path.relative_to(root)),
                            idx + 1,
                            symbol,
                            "guard block grants access instead of denying",
                        )
                    )
    return hits


def sweep_api(root: Path) -> list[Hit]:
    """Sweep B: API-layer guards that skip an authorization helper."""
    hits: list[Hit] = []
    for path in sorted((root / "src" / "api").rglob("*.py")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for idx, line in enumerate(lines):
            match = GUARD_POSITIVE.match(line)
            if not match:
                continue
            symbol = match.group(2)
            if not _is_dependency(symbol):
                continue
            block = _block_after(lines, idx)
            if not block:
                continue
            text = "\n".join(block)
            if not any(call in text for call in AUTHZ_CALLS):
                continue
            if _has_deny(block) and "raise" in text:
                # The guard body itself denies (optionally after best-effort
                # lookups), which is the fail-closed shape.
                continue
            rel = str(path.relative_to(root))
            if (rel, symbol) in REVIEWED_GUARDS:
                hits.append(
                    Hit(
                        NON_AUTH,
                        rel,
                        idx + 1,
                        symbol,
                        REVIEWED_GUARDS[(rel, symbol)],
                    )
                )
                continue
            hits.append(
                Hit(
                    REVIEW,
                    rel,
                    idx + 1,
                    symbol,
                    "authorization helper inside an `if <module>:` guard "
                    "with no denying branch",
                )
            )
    return hits


# ---------------------------------------------------------------------------
# Sweep E: manager write methods that take a caller id and never use it
# ---------------------------------------------------------------------------

# Parameter names that carry the acting caller's identity.
CALLER_PARAMS = frozenset(
    {
        "user_id",
        "actor_id",
        "admin_id",
        "requester_id",
        "sender_id",
        "owner_id",
        "viewer_id",
        "caller_id",
        "current_user_id",
        "reviewer_id",
        "moderator_id",
        "approved_by",
        "inviter_id",
        "acting_user_id",
    }
)

# Any other ``*_id`` parameter is treated as the resource being acted on.


def _is_resource_param(name: str) -> bool:
    return name == "id" or name.endswith("_id")


# Method-name tokens that make a method a mutation.
MANAGER_WRITE_VERBS = frozenset(
    {
        "create",
        "update",
        "delete",
        "add",
        "remove",
        "set",
        "edit",
        "rename",
        "move",
        "ban",
        "unban",
        "kick",
        "pin",
        "unpin",
        "assign",
        "grant",
        "revoke",
        "approve",
        "reject",
        "close",
        "reopen",
        "archive",
        "restore",
        "purge",
        "block",
        "unblock",
        "send",
        "post",
        "invite",
        "join",
        "leave",
        "publish",
        "cancel",
        "execute",
        "trigger",
        "rotate",
        "reset",
        "submit",
        "apply",
        "transfer",
        "mark",
    }
)

# Tokens proving the body resolves the caller's access: a permission/
# membership/ownership check, an explicit denial (raise PermissionDenied...),
# or a call that passes the caller id on to a layer that will check.
CORE_ACCESS_SIGNALS = (
    "has_permission",
    "get_permissions",
    "check_permission",
    "require_permission",
    "_check",
    "_require",
    "_verify",
    "_ensure",
    "_assert",
    "_validate",
    "is_member",
    "get_member",
    "is_participant",
    "is_owner",
    "owner_id",
    "author_id",
    "created_by",
    "uploaded_by",
    "uploader_id",
    "is_blocked",
    "can_view",
    "_can_",
    "authorize",
    "permission",
    "visibility",
    "visible",
    "is_admin",
    "has_role",
    "allowed",
    "roles",
    "_is_",
    "_may_",
    "PermissionDenied",
)

# Data layers that are not authorization boundaries (the caller above them
# authorizes), and event payload builders that never mutate anything.
MANAGER_SKIP_DIRS = ("/repositories/", "/events/")
MANAGER_SKIP_FILES = ("protocol.py",)

# Manager methods already inspected and confirmed safe. Key: (relative path,
# method name). Value: why the missing caller use is not a bug.
REVIEWED_METHODS: dict[tuple[str, str], str] = {
    (
        "src/core/admin/tickets.py",
        "update_ticket_status",
    ): (
        "data-layer helper reached only from the admin routes, which call "
        "require_admin_permission('tickets.close') first"
    ),
}


def _is_stub(node: ast.AST) -> bool:
    """True for a body that is only a docstring/``...``/``pass``."""
    for stmt in getattr(node, "body", []):
        if isinstance(stmt, ast.Pass):
            continue
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and (stmt.value.value is Ellipsis or isinstance(stmt.value.value, str))
        ):
            continue
        return False
    return True


def _type_checking_ranges(tree: ast.AST) -> list[tuple[int, int]]:
    """Line ranges of ``if TYPE_CHECKING:`` blocks (stub declarations)."""
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        name = (
            test.id
            if isinstance(test, ast.Name)
            else test.attr
            if isinstance(test, ast.Attribute)
            else ""
        )
        if name == "TYPE_CHECKING":
            ranges.append((node.lineno, getattr(node, "end_lineno", node.lineno)))
    return ranges


def _call_passes_caller(call: ast.Call, callers: frozenset[str]) -> bool:
    """True when the call forwards the caller id into another layer."""
    if not isinstance(call.func, (ast.Attribute, ast.Name)):
        return False
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        for inner in ast.walk(arg):
            if isinstance(inner, ast.Name) and inner.id in callers:
                return True
    return False


def sweep_managers(root: Path) -> list[Hit]:
    """Sweep E: mutating manager methods that ignore the caller identity.

    Sweeps A-C inspect the *guard* around an access check. This sweep covers
    the manager API surface itself: a method that takes both a caller id
    (``user_id``/``admin_id``/...) and a resource id but never uses the caller
    to resolve access -- no permission/membership/ownership read, no explicit
    denial, and no call that forwards the caller id -- is a candidate missing
    check (the manager-layer mirror of sweep D's ``UNAUTHZ``).
    """
    hits: list[Hit] = []
    core_dir = root / "src" / "core"
    if not core_dir.is_dir():
        return hits
    for path in sorted(core_dir.rglob("*.py")):
        posix = path.as_posix()
        if any(skip in posix for skip in MANAGER_SKIP_DIRS):
            continue
        if path.name in MANAGER_SKIP_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = str(path.relative_to(root))
        stubs = _type_checking_ranges(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if name.startswith("_") or _is_stub(node):
                continue
            if any(start <= node.lineno <= end for start, end in stubs):
                continue
            if not (set(name.split("_")) & MANAGER_WRITE_VERBS):
                continue
            params = [arg.arg for arg in node.args.args + node.args.kwonlyargs]
            callers = frozenset(p for p in params if p in CALLER_PARAMS)
            resources = [
                p for p in params if _is_resource_param(p) and p not in CALLER_PARAMS
            ]
            if not callers or not resources:
                continue
            source = ast.get_source_segment(text, node) or ""
            if any(sig in source for sig in CORE_ACCESS_SIGNALS):
                continue
            if any(
                _call_passes_caller(call, callers)
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
            ):
                continue
            if (rel, name) in REVIEWED_METHODS:
                hits.append(
                    Hit(
                        NON_AUTH,
                        rel,
                        node.lineno,
                        name,
                        REVIEWED_METHODS[(rel, name)],
                    )
                )
                continue
            hits.append(
                Hit(
                    UNCHECKED,
                    rel,
                    node.lineno,
                    name,
                    "mutating method takes a caller id and a resource id but "
                    "never uses the caller to resolve access",
                )
            )
    return hits


# Function names that make an authorization decision on the client.
CLIENT_AUTH_FN = re.compile(
    r"(?:function|const|var|let)\s+"
    r"(can(?:Edit|Delete|Manage|View|Moderate|Post)|has(?:Permission|Access)|is(?:Owner|Admin|Moderator))"
    r"\s*[=(]"
)

# Tokens that identify a body as an access decision rather than a UI helper.
CLIENT_AUTH_TOKENS = (
    "permission",
    "author_id",
    "owner_id",
    "my_permissions",
    "can_edit",
    "role",
)

# Sources a real client access decision must consult: the server permission
# projection (under any of its names), an ownership id, or a role list.
CLIENT_PERMISSION_SOURCES = (
    "my_permissions",
    "permissions",
    "hasPermission",
    "has_permission",
    "owner_id",
    "author_id",
    "administrator",
    "role",
)

FAIL_OPEN = re.compile(r"if \(!\s*[\w.]*\w\s*\)\s*return true")
FAIL_CLOSED = re.compile(r"if \(!\s*[\w.]*\w\s*\)\s*return false")
INLINE_DELEGATE = re.compile(r"^\s*return\s+(ctx|window|this)\.\w+\(")


def sweep_client(client_root: Path) -> list[Hit]:
    """Sweep C: client access-decision helpers that fail open.

    Only bodies that actually decide access (they touch a permission, an
    ownership id, or the server ``my_permissions`` projection) are reported;
    generic UI helpers that happen to contain ``if (!x) return true`` (scroll
    heuristics, no-op checks) are not access decisions.
    """
    hits: list[Hit] = []
    js_root = client_root / "src" / "js"
    if not js_root.is_dir():
        return hits
    for path in sorted(js_root.rglob("*.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(client_root))
        for match in CLIENT_AUTH_FN.finditer(text):
            name = match.group(1)
            body = text[match.start() : match.start() + 1500]
            # A pure delegate to another implementation is not a decision site.
            if INLINE_DELEGATE.match(body.split(name, 1)[-1].split("\n", 1)[0]):
                continue
            if not any(token in body for token in CLIENT_AUTH_TOKENS):
                continue
            line_no = text[: match.start()].count("\n") + 1
            if FAIL_OPEN.search(body):
                hits.append(
                    Hit(
                        GRANT,
                        rel,
                        line_no,
                        name,
                        "unknown viewer is treated as authorized",
                    )
                )
            elif FAIL_CLOSED.search(body):
                hits.append(
                    Hit(
                        NON_AUTH,
                        rel,
                        line_no,
                        name,
                        "unknown viewer denied (fail closed)",
                    )
                )
            if not any(src in body for src in CLIENT_PERMISSION_SOURCES):
                hits.append(
                    Hit(
                        REVIEW,
                        rel,
                        line_no,
                        name,
                        "access helper consults no permission, ownership, or role data",
                    )
                )
    return hits


# ---------------------------------------------------------------------------
# Sweep F: private helpers probed off a module-or-manager facade
# ---------------------------------------------------------------------------

# A probe is ``getattr(<expr>, "_name", <default>)``. When the attribute lives
# on the manager but the object handed in is the module facade (or vice versa),
# the probe silently yields the default. If the enclosing function is an
# authorization guard, that default turns the check into a no-op.

PROBE_AUTHZ_TOKENS = (
    "permission",
    "access",
    "member",
    "role",
    "owner",
    "block",
    "authoriz",
    "authoris",
    "admin",
)

AUTHZ_FN_RE = re.compile(
    r"(permission|require|authoriz|authoris|check_access|_can_|^can_)",
    re.IGNORECASE,
)

# Facade probes inspected and confirmed non-authoritative. Key: (relative
# path, probed attribute). Value: why the ``None`` branch is not a bypass.
REVIEWED_PROBES: dict[tuple[str, str], str] = {
    ("src/core/voice/manager/calls.py", "_db"): (
        "auto-transcription feature probe; skipping the scheduling call is "
        "not an access decision"
    ),
    ("src/core/voice/signaling/sfu/base.py", "_quality_mixin"): (
        "optional quality sink; skipping metric reporting is not an access decision"
    ),
}


def _fn_is_authz_named(name: str) -> bool:
    return bool(AUTHZ_FN_RE.search(name))


def _probe_name_has_authz_token(name: str) -> bool:
    lowered = name.lstrip("_").lower()
    return any(token in lowered for token in PROBE_AUTHZ_TOKENS)


def _probe_attr(call: ast.AST) -> str | None:
    """Return the private attribute name a ``getattr`` probe requests."""
    if not isinstance(call, ast.Call):
        return None
    if not isinstance(call.func, ast.Name) or call.func.id != "getattr":
        return None
    if len(call.args) < 2:
        return None
    attr = call.args[1]
    if not isinstance(attr, ast.Constant) or not isinstance(attr.value, str):
        return None
    if not attr.value.startswith("_"):
        return None
    return attr.value


def _probe_locals(fn: ast.AST) -> dict[str, tuple[str, int]]:
    """Map ``name = getattr(obj, "_helper", ...)`` locals to their probe."""
    found: dict[str, tuple[str, int]] = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        attr = _probe_attr(node.value)
        if attr is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found[target.id] = (attr, node.lineno)
    return found


def _branch_skips(body: list[ast.stmt]) -> bool:
    """True when a branch bails out without taking a decision.

    Only a bare ``return`` / ``return None`` / ``continue`` / ``break``
    counts. ``return True``, ``return False``, ``return []`` and ``raise`` are
    decisions -- the shape an authorization guard must have.
    """
    if any(isinstance(stmt, ast.Raise) for stmt in body):
        return False
    for stmt in body:
        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                return True
        elif isinstance(stmt, (ast.Continue, ast.Break)):
            return True
    return False


def _test_references_probe(
    test: ast.AST, probes: dict[str, tuple[str, int]]
) -> list[str]:
    """Probed attributes the ``if`` test scrutines (directly or via a local)."""
    used: list[str] = []
    for node in ast.walk(test):
        if isinstance(node, ast.Name) and node.id in probes:
            used.append(probes[node.id][0])
        else:
            attr = _probe_attr(node)
            if attr is not None:
                used.append(attr)
    return used


def _probe_is_called(fn: ast.AST, probes: dict[str, tuple[str, int]]) -> bool:
    """True when the function actually invokes one of the probed helpers."""
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in probes
        ):
            return True
    return False


def sweep_facades(root: Path) -> list[Hit]:
    """Sweep F: auth guards that skip when a private helper is unresolvable."""
    hits: list[Hit] = []
    for path in sorted((root / "src").rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="replace")
        # Fast path: facade probes are always `getattr(obj, "_name", ...)`
        # calls, so a file without `getattr` cannot contain one. This
        # skips the parse plus several AST walks for ~85% of the tree.
        if "getattr" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        rel = str(path.relative_to(root))
        skip_ranges = _type_checking_ranges(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(start <= fn.lineno <= end for start, end in skip_ranges):
                continue
            probes = _probe_locals(fn)
            if not probes:
                continue
            calls_helper = _probe_is_called(fn, probes)
            authz_fn = _fn_is_authz_named(fn.name)
            for node in ast.walk(fn):
                if not isinstance(node, ast.If) or not _branch_skips(node.body):
                    continue
                attrs = _test_references_probe(node.test, probes)
                if not attrs:
                    continue
                for attr in attrs:
                    key = (rel, attr)
                    if key in REVIEWED_PROBES:
                        hits.append(
                            Hit(
                                NON_AUTH,
                                rel,
                                node.lineno,
                                attr,
                                REVIEWED_PROBES[key],
                            )
                        )
                        continue
                    if authz_fn and calls_helper:
                        hits.append(
                            Hit(
                                GRANT,
                                rel,
                                node.lineno,
                                attr,
                                f"authorization guard `{fn.name}` skips its check "
                                "when the private helper cannot be resolved",
                            )
                        )
                    elif _probe_name_has_authz_token(attr):
                        hits.append(
                            Hit(
                                REVIEW,
                                rel,
                                node.lineno,
                                attr,
                                "private authorization helper resolved with a "
                                "`None` default; the missing branch skips",
                            )
                        )
                    else:
                        hits.append(
                            Hit(
                                NON_AUTH,
                                rel,
                                node.lineno,
                                attr,
                                "private attribute probe; not an access decision",
                            )
                        )
    return hits


# ---------------------------------------------------------------------------
# Sweep G: SDP answers whose ICE/DTLS credentials are fabricated
# ---------------------------------------------------------------------------

# Generators that mint a fresh, uncorrelatable value on every call. When one
# of these fills an ICE ufrag/pwd or a DTLS fingerprint the SFU never supplied,
# the answer looks negotiable while no media can ever flow: the client is told
# the call connected and silently sits on a dead session.
FABRICATED = "FABRICATED"

RANDOM_CREDENTIAL_CALLS: dict[str, frozenset[str]] = {
    "secrets": frozenset({"token_hex", "token_bytes", "token_urlsafe", "randbits"}),
    "os": frozenset({"urandom"}),
    "uuid": frozenset({"uuid1", "uuid4"}),
    "random": frozenset(
        {"random", "randbytes", "getrandbits", "randint", "randrange", "choice"}
    ),
}

# Strings that only appear while assembling an SDP answer.
SDP_MARKERS = ("a=ice-ufrag:", "a=ice-pwd:", "a=fingerprint:")


def _random_credential_call(node: ast.AST) -> str | None:
    """Return ``module.func`` when the node mints a fresh random value."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if not isinstance(node.func.value, ast.Name):
        return None
    funcs = RANDOM_CREDENTIAL_CALLS.get(node.func.value.id)
    if funcs and node.func.attr in funcs:
        return f"{node.func.value.id}.{node.func.attr}"
    return None


def sweep_sdp_fabrication(root: Path) -> list[Hit]:
    """Sweep G: SDP answers that invent the ICE/DTLS credentials they need.

    Scoped tightly to functions that actually assemble an SDP answer (their
    source contains an ICE/fingerprint attribute line) AND call a random
    generator. Randomness elsewhere -- session ids, tokens, nonces -- is not
    reported.
    """
    hits: list[Hit] = []
    for path in sorted((root / "src").rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="replace")
        if not any(marker in source for marker in SDP_MARKERS):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        rel = str(path.relative_to(root))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            segment = ast.get_source_segment(source, fn) or ""
            if not any(marker in segment for marker in SDP_MARKERS):
                continue
            for node in ast.walk(fn):
                call = _random_credential_call(node)
                if call is None:
                    continue
                hits.append(
                    Hit(
                        FABRICATED,
                        rel,
                        getattr(node, "lineno", fn.lineno),
                        call,
                        f"`{fn.name}` fills a missing ICE/DTLS credential with "
                        "a fresh random value; the client is handed an answer "
                        "that can never connect",
                    )
                )
    return hits


# ---------------------------------------------------------------------------
# Sweep H: media callbacks the client sends that no handler ever reads
# ---------------------------------------------------------------------------

# A field is only "undelivered" if the client sends it on an opcode the server
# does handle. These are the fields individually verified as informational --
# the server resolves everything they could influence from the authenticated
# caller instead, so dropping one is not a gap.
REVIEWED_CALLBACK_FIELDS: dict[str, str] = {
    "target_user_id": (
        "the SFU/media peer is always resolved from the authenticated sender's "
        "own connection; a client-chosen peer id is never honored"
    ),
    "type": (
        "the SDP role comes from the opcode (offer vs answer); the server sets "
        "the SDPMessage type itself"
    ),
}

# Opcode-specific verdicts: (opcode name, field) -> why the field is unused.
REVIEWED_CALLBACK_FIELDS_BY_OPCODE: dict[tuple[str, str], str] = {
    ("VOICE_DISCONNECT", "channel_id"): (
        "the opcode drops only the caller's own session; the channel is "
        "resolved from their persisted voice state"
    ),
    ("VOICE_SPEAKING", "acoustic_defense"): (
        "client-local speaking-detection threshold; the server-side acoustic "
        "defense is deployment config applied when the SFU adapter is built"
    ),
}

# ``this._sendVoiceMessage(this.OPCODES.NAME, {k: v, ...})``
_SEND_VOICE_CALL_RE = re.compile(
    r"_sendVoiceMessage\(\s*this\.OPCODES\.([A-Z0-9_]+)\s*,\s*\{(?P<body>[^}]*)\}",
    re.DOTALL,
)
_OBJECT_KEY_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*:")


def _client_voice_sends(
    client_root: Path,
) -> tuple[list[tuple[str, str, str, int]], bool]:
    """``(opcode, field, client-relative path, line)`` for every send payload.

    Returns ``available=False`` when the client checkout cannot be found, so
    the caller reports a skip rather than an empty (and falsely clean) pass.
    """
    js_root = client_root / "src"
    if not js_root.is_dir():
        return [], False

    found: list[tuple[str, str, str, int]] = []
    for path in sorted(js_root.rglob("*.js")):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "_sendVoiceMessage" not in source:
            continue
        rel = f"{client_root.name}/{path.relative_to(client_root)}"
        for match in _SEND_VOICE_CALL_RE.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            for key in _OBJECT_KEY_RE.findall(match.group("body")):
                found.append((match.group(1), key, rel, line))
    return found, bool(found)


def _handler_payload_reads(root: Path) -> dict[str, set[str]]:
    """Map ``handle_voice_*`` function name -> payload keys it reads."""
    path = root / "src" / "api" / "websocket" / "handlers" / "voice.py"
    if not path.is_file():
        return {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return {}

    reads: dict[str, set[str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not fn.name.startswith("handle_voice_"):
            continue
        keys: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "data"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    keys.add(node.args[0].value)
            elif isinstance(node, ast.Subscript):
                if (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "data"
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)
                ):
                    keys.add(node.slice.value)
        reads[fn.name] = keys
    return reads


def sweep_callback_delivery(root: Path, client_root: Path) -> tuple[list[Hit], bool]:
    """Sweep H: client-sent media fields no server handler consumes."""
    sends, available = _client_voice_sends(client_root)
    if not available:
        return [], False

    reads = _handler_payload_reads(root)
    if not reads:
        return [], False

    hits: dict[tuple[str, str], Hit] = {}
    for opcode, field, rel, line in sends:
        handler_reads = reads.get(f"handle_{opcode.lower()}")
        if handler_reads is None:
            # No server handler for the opcode at all -- a different class,
            # covered by the route/handler inventory (sweep D).
            continue
        if field in handler_reads:
            continue
        if field in REVIEWED_CALLBACK_FIELDS:
            continue
        if (opcode, field) in REVIEWED_CALLBACK_FIELDS_BY_OPCODE:
            continue
        hits.setdefault(
            (opcode, field),
            Hit(
                UNDELIVERED,
                rel,
                line,
                f"{opcode}.{field}",
                f"the client sends `{field}` on {opcode} and its gateway handler "
                "never reads it: the callback is sent and never delivered",
            ),
        )

    return sorted(hits.values(), key=lambda h: (h.symbol, h.path)), True


# ---------------------------------------------------------------------------
# Sweep D: route / websocket-handler authorization inventory
# ---------------------------------------------------------------------------

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
MUTATING = ("POST", "PUT", "PATCH", "DELETE")

# Authentication mechanisms: any one of these proves the caller identity is
# resolved before (or by) the handler.
AUTHN_SIGNALS = (
    "Depends(get_current_user",
    "Depends(get_optional_user",
    "Depends(get_admin_access",
    "Depends(get_admin_user",
    "Depends(get_admin",
    "Depends(require_",
    "get_current_user",
    "get_optional_user",
    "get_admin_access",
    "get_admin_user",
    "get_admin_from_token",
    "require_admin_permission",  # authenticates then checks a scope
    "_require_admin",  # module-local admin gate
    "check_host_restriction",  # admin-surface host/UI gate
    "_authenticate(",  # federation HMAC shared-key credential
    "is_self_test_request",
)

# Credential verification: an endpoint that verifies a presented secret is
# itself the authentication step (login, identify, refresh) and therefore both
# authenticates and gates the request.
CREDENTIAL_SIGNALS = (
    "_verify_token",
    "verify_password",
    "check_password",
    "validate_token",
    "verify_totp",
    "verify_refresh",
    "verify_signature",
    "_authenticate(",
    "verify_challenge",
    "verify_email",
    "verify_code",
    "verify_otp",
    "verify_2fa",
    "consume_token",
    "reset_password",
)

# Signals that the handler consumes the resolved caller identity, so an
# ownership decision is reachable from the body itself. The parameter is not
# always named ``current_user`` (``user``, ``token`` ...), so attribute access
# on the authenticated id is also matched.
IDENTITY_SIGNALS = (
    "current_user",
    "admin_id",
    "request.state.admin",
    "request.state.user",
)

IDENTITY_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.(user_id|admin_id)\b")

# Explicit authorization signals: permission checks, membership/role lookups,
# resource-scope resolution, host restriction.
AUTHZ_SIGNALS = (
    "has_permission",
    "get_permissions",
    "require_permission",
    "require_admin_permission",
    "_require_admin",
    "check_admin_permission",
    "_check_admin",
    "is_participant",
    "is_ip_blocked",
    "is_owner",
    "owner_id",
    "require_manage",
    "has_feature",
    # Helper-name signals are anchored on the call so a variable like
    # ``not_required`` cannot masquerade as an authorization check.
    "_require(",
    "_check_(",
    "_ensure_(",
    "_verify_(",
    "_assert_(",
    "_has_(",
    "_authenticate(",
    "resolve_artifact_can",
    "get_channel",
    "get_server",
    "get_conversation",
    "get_member",
    "channel_exists",
    "conversation_exists",
    "raise_forbidden",
    "raise_not_found",
    "status_code=403",
    "session_admin_id",
    "_authorize(",
    "_guard(",
    "_gate(",
)

# Mutating routes whose declared path is scoped to the caller ("@me"), so the
# resource IS the caller and no separate authorization call is required.
SELF_SCOPED_TOKENS = ("@me",)

# Whole modules verified public / credentialed by a non-user mechanism. These
# are pre-authentication surfaces (login, probes, signed inbound traffic), so
# the absence of a `get_current_user` dependency is by design.
PUBLIC_FILES: dict[str, str] = {
    "src/api/routes/health.py": "liveness/readiness probes are unauthenticated",
    "src/api/routes/version.py": "version negotiation is public",
    "src/api/routes/config.py": "public capability advertisement",
    "src/api/routes/qr.py": "public QR helper",
    "src/api/routes/help.py": "static help/robots pages",
    "src/api/routes/public_license.py": "public license challenge/info",
    "src/api/routes/federation.py": (
        "inbound cross-instance traffic authenticated by HMAC shared key"
    ),
    "src/api/routes/auth/__init__.py": "auth router aggregation",
    "src/api/routes/docs/router/composer.py": (
        "public documentation pages, mounted only when docs are enabled"
    ),
}

# Individual routes verified public / credentialed by a non-user mechanism.
# Key: (relative path, METHOD, declared path literal). Value: why it is safe.
PUBLIC_BY_DESIGN: dict[tuple[str, str, str], str] = {
    # Pre-authentication account lifecycle: the caller has no session yet, so
    # the endpoint's own credential exchange is the gate.
    ("src/api/routes/auth/login.py", "POST", "/login"): (
        "pre-authentication credential exchange"
    ),
    ("src/api/routes/auth/register.py", "POST", "/register"): ("public registration"),
    ("src/api/routes/auth/sessions.py", "POST", "/refresh"): (
        "credentialed by the refresh token presented in the request"
    ),
    ("src/api/routes/auth/two_factor.py", "POST", "/2fa"): (
        "pre-authentication second-factor exchange"
    ),
    ("src/api/routes/auth/oauth.py", "GET", "/oauth/{provider}/login"): (
        "OAuth login redirect"
    ),
    ("src/api/routes/auth/oauth.py", "POST", "/oauth/{provider}/callback"): (
        "OAuth callback credentialed by the provider code and state"
    ),
    (
        "src/api/routes/auth/passkeys.py",
        "POST",
        "/passkeys/options/authenticate",
    ): "issues a WebAuthn authentication challenge",
    ("src/api/routes/auth/password.py", "GET", "/password-requirements"): (
        "public password policy"
    ),
    ("src/api/routes/auth/password.py", "POST", "/password-reset/request"): (
        "enumeration-safe reset request"
    ),
    ("src/api/routes/auth/password.py", "POST", "/password-reset/confirm"): (
        "credentialed by the emailed reset token"
    ),
    (
        "src/api/routes/auth/email_verification.py",
        "POST",
        "/resend-verification",
    ): "enumeration-safe resend; no caller identity exists yet",
    # Public read-only resources.
    ("src/api/routes/avatars.py", "GET", "/users/{user_id}"): ("public avatar image"),
    ("src/api/routes/avatars.py", "GET", "/servers/{server_id}"): (
        "public server icon image"
    ),
    ("src/api/routes/bots.py", "GET", "/profiles/{application_id}"): (
        "public bot profile"
    ),
    ("src/api/routes/bots.py", "GET", "/directory"): "public bot directory",
    ("src/api/routes/telemetry.py", "POST", "/csp-report"): (
        "browser-submitted CSP violation report"
    ),
    # Credentialed by an opaque per-resource token, not a user session.
    ("src/api/routes/webhooks.py", "POST", "/{webhook_id}/{token}"): (
        "inbound webhook credentialed by the per-webhook token"
    ),
    (
        "src/api/routes/applications.py",
        "POST",
        "/interactions/{interaction_token}/callback",
    ): "credentialed by the signed interaction token",
    # Static UI redirects (the pages they point at enforce the host gate).
    ("src/api/routes/admin/ui.py", "GET", "/"): (
        "static redirect to the admin login page"
    ),
    ("src/api/routes/admin/ui.py", "GET", "/ui"): (
        "static redirect to the admin dashboard page"
    ),
}

# Routes verified as authorized inside a helper the handler calls.
REVIEWED_OK: dict[tuple[str, str, str], str] = {
    ("src/api/routes/admin/auth.py", "POST", "/logout"): (
        "self-scoped: revokes only the token the caller already presents"
    ),
}


def _string_literal(node: ast.AST) -> str | None:
    """Return the value of a string constant node, else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _router_decorator(dec: ast.AST) -> tuple[str, str] | None:
    """Return ``(METHOD, path)`` for a ``@router.<method>(...)`` decorator."""
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not isinstance(func, ast.Attribute) or func.attr.lower() not in HTTP_METHODS:
        return None
    if not isinstance(func.value, ast.Name):
        return None
    if func.value.id not in ("router", "app", "api_router"):
        return None
    path = next(
        (value for value in (_string_literal(a) for a in dec.args) if value),
        "<dynamic>",
    )
    return func.attr.upper(), path


def _build_handler_index(
    routes_dir: Path, root: Path
) -> dict[str, list[tuple[str, str]]]:
    """Map every route-module function name to its source segments."""
    index: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(routes_dir.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            continue
        rel = str(path.relative_to(root))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index.setdefault(node.name, []).append(
                    (rel, ast.get_source_segment(text, node) or "")
                )
    return index


def _resolve_handler(
    name: str, decl_rel: str, handlers: dict[str, list[tuple[str, str]]]
) -> str:
    """Resolve a registered handler name to source, preferring siblings."""
    if not name:
        return ""
    candidates = handlers.get(name)
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0][1]
    decl_dir = Path(decl_rel).parent
    for rel, source in candidates:
        if Path(rel).parent == decl_dir:
            return source
    for rel, source in candidates:
        if Path(rel).parent.parent == decl_dir.parent:
            return source
    return candidates[0][1]


def _iter_route_sources(
    tree: ast.AST,
    text: str,
    decl_rel: str,
    handlers: dict[str, list[tuple[str, str]]],
) -> list[tuple[str, str, str, int, bool]]:
    """Yield ``(METHOD, path, source, lineno, resolved)`` for every route.

    Covers the decorator form ``@router.get(...)`` and the direct-registration
    form ``router.get(...)(handler)`` used by the ``*Router`` composers. For
    the latter the handler body usually lives in a mixin in another module, so
    it is resolved through ``handlers``.
    """
    declared: list[tuple[str, str, str, int, bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                found = _router_decorator(dec)
                if found:
                    source = ast.get_source_segment(text, node) or ""
                    declared.append(
                        (found[0], found[1], source, node.lineno, bool(source))
                    )
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            inner = node.value.func
            if not isinstance(inner, ast.Call):
                continue
            found = _router_decorator(inner)
            if not found:
                continue
            target = ""
            if node.value.args:
                arg = node.value.args[0]
                if isinstance(arg, ast.Name):
                    target = arg.id
                elif isinstance(arg, ast.Attribute):
                    target = arg.attr
            source = _resolve_handler(target, decl_rel, handlers)
            declared.append(
                (found[0], found[1], source, node.value.lineno, bool(source))
            )
    return declared


def sweep_routes(root: Path) -> list[Hit]:
    """Sweep D: inventory every FastAPI route's authorization surface.

    Reports the two "missing check" shapes:

    * ``NO-AUTH`` -- a route with no authentication mechanism at all.
    * ``UNAUTHZ`` -- a mutating route that resolves the caller but never uses
      that identity (and makes no visible permission/membership/scope call),
      i.e. a candidate missing authorization check.
    """
    hits: list[Hit] = []
    routes_dir = root / "src" / "api" / "routes"
    if not routes_dir.is_dir():
        return hits
    handlers = _build_handler_index(routes_dir, root)
    for path in sorted(routes_dir.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(root))
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            hits.append(
                Hit(
                    UNAUTHZ,
                    rel,
                    1,
                    "<unparsable module>",
                    f"server module could not be parsed: {exc}",
                )
            )
            continue
        for method, route_path, source, line_no, resolved in _iter_route_sources(
            tree, text, rel, handlers
        ):
            key = (rel, method, route_path)
            reason = PUBLIC_BY_DESIGN.get(key)
            if reason is None and rel in PUBLIC_FILES:
                reason = PUBLIC_FILES[rel]
            symbol = f"{method} {route_path}"
            if reason is not None:
                hits.append(Hit(PUBLIC, rel, line_no, symbol, reason))
                continue
            if key in REVIEWED_OK:
                hits.append(Hit(PUBLIC, rel, line_no, symbol, REVIEWED_OK[key]))
                continue
            if not resolved or not source:
                hits.append(
                    Hit(
                        REVIEW,
                        rel,
                        line_no,
                        symbol,
                        "handler source could not be resolved; authorization "
                        "surface unknown",
                    )
                )
                continue
            credential = any(sig in source for sig in CREDENTIAL_SIGNALS)
            if credential:
                hits.append(
                    Hit(
                        PUBLIC,
                        rel,
                        line_no,
                        f"{method} {route_path}",
                        "auth-establishing endpoint: verifies a presented credential",
                    )
                )
                continue
            authenticated = any(sig in source for sig in AUTHN_SIGNALS)
            # Strip dependency expressions first: the dependency *name*
            # (``Depends(get_current_user)``) contains ``current_user`` and
            # would otherwise masquerade as a use of the caller identity.
            body = re.sub(r"Depends\([^()]*\)", "", source)
            uses_identity = IDENTITY_RE.search(body) is not None or any(
                sig in body for sig in IDENTITY_SIGNALS
            )
            authorized = (
                credential
                or uses_identity
                or any(sig in source for sig in AUTHZ_SIGNALS)
            )
            self_scoped = any(tok in route_path for tok in SELF_SCOPED_TOKENS)
            if not authenticated:
                hits.append(
                    Hit(
                        NO_AUTH,
                        rel,
                        line_no,
                        symbol,
                        "no authentication dependency on this route",
                    )
                )
                continue
            if method in MUTATING and not authorized and not self_scoped:
                hits.append(
                    Hit(
                        UNAUTHZ,
                        rel,
                        line_no,
                        symbol,
                        "mutating route resolves the caller but never uses that "
                        "identity or any permission/scope check",
                    )
                )
    return hits


def sweep_ws_handlers(root: Path) -> list[Hit]:
    """Sweep D: websocket opcode handlers with no authorization surface."""
    hits: list[Hit] = []
    handlers_dir = root / "src" / "api" / "websocket" / "handlers"
    if not handlers_dir.is_dir():
        return hits
    for path in sorted(handlers_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(root))
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if not name.startswith("handle_") or "heartbeat" in name.lower():
                continue
            body = ast.get_source_segment(text, node) or ""
            credential = any(sig in body for sig in CREDENTIAL_SIGNALS)
            authenticated = credential or (
                "is_authenticated" in body or "NOT_AUTHENTICATED" in body
            )
            if not authenticated:
                hits.append(
                    Hit(
                        NO_AUTH,
                        rel,
                        node.lineno,
                        name,
                        "opcode handler does not check connection authentication",
                    )
                )
                continue
            detail = (
                "auth-establishing opcode: verifies a presented credential"
                if credential
                else None
            )
            authorized = credential or any(sig in body for sig in AUTHZ_SIGNALS)
            # Authorization for gateway opcodes is delegated to the owning
            # manager (`self._<dep>` / `dispatcher.`), which the core sweeps
            # verify fails closed when its dependency is unwired.
            delegates = (
                bool(re.search(r"\b(self|dispatcher|mgr|manager|signaling)\.", body))
                or "from src.core" in body
            )
            if authorized or delegates:
                hits.append(
                    Hit(
                        PUBLIC,
                        rel,
                        node.lineno,
                        name,
                        detail or "authenticated; authorization delegated to manager",
                    )
                )
            else:
                hits.append(
                    Hit(
                        REVIEW,
                        rel,
                        node.lineno,
                        name,
                        "authenticated opcode handler with no visible "
                        "authorization or delegation",
                    )
                )
    return hits


# ---------------------------------------------------------------------------
# Sweep I: an authorization decision taken from a possibly-empty listing
# ---------------------------------------------------------------------------

# Sweeps A-H ask whether a check *runs* and whether its result is delivered.
# This sweep is the threads-listing shape fixed in ANNEX AB: a permission whose
# input is a collection the function itself fetched, so the decision is derived
# from the data instead of taken about the caller -- an empty listing answers a
# question nobody asked, and a listing that happens to hold no public rows can
# skip the check entirely.
EMPTY_DECIDED = "EMPTY-DECIDED"

# Readers that return a *collection*. A scalar presence check
# (``row = fetch_one(...)``; ``if conv_id:``) is deliberately out of scope:
# gating an access call on "the resource exists" is a normal, fail-closed
# pattern, and treating it as a bug buries the real shape in noise.
COLLECTION_READERS = frozenset(
    {
        "fetch_all",
        "fetchall",
        "fetch_many",
        "query_all",
        "query_many",
        "select",
        "select_all",
        "search",
    }
)

COLLECTION_PREFIXES = ("list_", "get_all", "get_many", "find_all", "load_all")

# Calls that fold a collection into one flag -- ``any(x for x in rows)`` --
# which is what a guard over "does the listing hold the relevant rows" looks
# like in practice.
FLAG_CALLS = frozenset({"any", "all", "len", "bool", "sum", "count"})

_AUTHZ_NAME = re.compile(
    r"(permission|is_?member|has_role|has_permission|can_|authoriz|require_)"
)

# Hits individually verified as *not* the skipped-decision shape. Key:
# (relative path, function name). Value: why the listing does not decide access.
REVIEWED_EMPTY_DECIDED: dict[tuple[str, str], str] = {}


def _unwrap_await(node: ast.AST) -> ast.AST:
    """Strip ``await`` so an async fetch classifies as the call it wraps.

    API handlers are ``async def`` and bind their listings with
    ``rows = await self._db.fetch_all(...)``, whose value is an ``ast.Await``.
    Without unwrapping, the core-layer sweep sees the collection but the API
    layer never does, so the shape it exists to catch would be invisible in the
    very layer the extension targets.
    """
    while isinstance(node, ast.Await):
        node = node.value
    return node


def _simple_call_name(node: ast.AST) -> str:
    """The bare name of a call (``self._db.fetch_all`` -> ``fetch_all``)."""
    node = _unwrap_await(node)
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_collection_reader(node: ast.AST) -> bool:
    name = _simple_call_name(node)
    if not name:
        return False
    return name in COLLECTION_READERS or name.startswith(COLLECTION_PREFIXES)


def _is_authz_call(node: ast.AST) -> bool:
    """True for a call that looks like an access decision."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if not name:
        return False
    if name in AUTHZ_CALLS:
        return True
    return bool(_AUTHZ_NAME.search(name))


def _loaded_names(node: ast.AST) -> set[str]:
    """Names read (not assigned) anywhere inside ``node``."""
    return {
        sub.id
        for sub in ast.walk(node)
        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
    }


def _call_argument_names(call: ast.Call) -> set[str]:
    names: set[str] = set()
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        names |= _loaded_names(arg)
    return names


def _collection_and_row_names(fn: ast.AST) -> tuple[set[str], set[str]]:
    """Names in ``fn`` bound to a fetched collection, and to a row of one.

    Collections: ``rows = fetch_all(...)``, a comprehension or accumulator
    built from one, and the flag a guard folds one into (``any(x for x in
    rows)``). Rows: the loop target of an iteration over a collection (or the
    single row pulled out of it with ``rows[0]`` / ``next(...)``), which is
    the value an access decision must never be derived from.
    """
    collections: set[str] = set()
    rows: set[str] = set()
    comps = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    for _ in range(5):  # fixpoint; five hops is past any real derivation
        changed = False
        for node in ast.walk(fn):
            targets: list[str] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                value = _unwrap_await(node.value)
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value = _unwrap_await(node.value)
                if isinstance(node.target, ast.Name):
                    targets = [node.target.id]
            if value is not None and targets:
                reads = _loaded_names(value)
                collection = False
                if _is_collection_reader(value) or (
                    isinstance(value, comps) and (reads & collections)
                ):
                    collection = True
                elif _simple_call_name(value) in FLAG_CALLS:
                    collection = bool(reads & collections)
                elif reads & collections:
                    # A value rebuilt out of a collection stays a listing; a
                    # scalar extracted from one is a row, not a collection.
                    collection = isinstance(value, (comps, ast.List, ast.Set))
                if collection:
                    for target in targets:
                        if target not in collections:
                            collections.add(target)
                            changed = True
                # ``x = rows[0]`` / ``x = next(t for t in rows)``: a single row.
                is_row = False
                if isinstance(value, ast.Subscript):
                    is_row = bool(_loaded_names(value.value) & collections)
                elif _simple_call_name(value) == "next":
                    is_row = bool(reads & collections)
                if is_row:
                    for target in targets:
                        if target not in rows:
                            rows.add(target)
                            changed = True
            # ``for row in rows:`` binds a row, and any accumulator it appends
            # to (``out.append(row)``) is itself a listing built from one.
            if isinstance(node, ast.For) and (_loaded_names(node.iter) & collections):
                if isinstance(node.target, ast.Name) and node.target.id not in rows:
                    rows.add(node.target.id)
                    changed = True
                for sub in ast.walk(node):
                    if not isinstance(sub, ast.Call):
                        continue
                    if not isinstance(sub.func, ast.Attribute):
                        continue
                    if sub.func.attr not in ("append", "add", "update", "extend"):
                        continue
                    base = sub.func.value
                    if isinstance(base, ast.Name) and base.id not in collections:
                        collections.add(base.id)
                        changed = True
        if not changed:
            break
    return collections, rows


def sweep_empty_decided(root: Path) -> list[Hit]:
    """Sweep I: authorization decided from a listing the function fetched.

    The threads-listing shape fixed in ANNEX AB: the access call sat inside a
    branch guarded by a flag folded over a listing the function had fetched
    (``if public_exists and self._servers:``), and its argument was a row
    pulled out of that same listing (``first_public.server_id``). The decision
    therefore came from the data rather than from the caller, and a listing
    that happened to hold no relevant rows answered a question nobody asked.

    The sweep covers the manager layer (``src/core``) *and* the API layer
    (``src/api``): a route handler that fetches rows and then authorizes from
    one of them carries the same skipped-decision hazard as the manager that
    did it -- the answer describes the listing, not the caller.

    A hit needs both halves -- the guard folds a *collection* (not a scalar
    existence check, which is a normal fail-closed pattern) and the access
    call consumes a *row* of it -- so a resource lookup authorizing against
    the resource it fetched is not reported.
    """
    hits: list[Hit] = []
    for scan_dir in (root / "src" / "core", root / "src" / "api"):
        if not scan_dir.is_dir():
            continue
        for path in sorted(scan_dir.rglob("*.py")):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            rel = str(path.relative_to(root))
            stubs = _type_checking_ranges(tree)
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if _is_stub(fn):
                    continue
                if any(start <= fn.lineno <= end for start, end in stubs):
                    continue
                if (rel, fn.name) in REVIEWED_EMPTY_DECIDED:
                    continue
                collections, rows = _collection_and_row_names(fn)
                if not collections or not rows:
                    continue
                for node in ast.walk(fn):
                    if not isinstance(node, ast.If):
                        continue
                    guard = _loaded_names(node.test) & collections
                    if not guard:
                        continue
                    for call in ast.walk(node):
                        if not _is_authz_call(call):
                            continue
                        used = _call_argument_names(call) & rows
                        if not used:
                            continue
                        hits.append(
                            Hit(
                                EMPTY_DECIDED,
                                rel,
                                call.lineno,
                                _simple_call_name(call),
                                f"`{fn.name}`: `{sorted(used)[0]}`, a row of the "
                                f"fetched listing `{sorted(guard)[0]}`, decides the "
                                "access check that guards it",
                            )
                        )
    return hits


def findings_inventory(findings_path: Path) -> list[str]:
    """Summarise the recorded findings inventory, if present."""
    if not findings_path.is_file():
        return ["findings file not found: " + str(findings_path)]
    text = findings_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    status = [ln for ln in lines if ">> STATUS:" in ln]
    fixed = sum(1 for ln in status if re.search(r"STATUS:[^\n]*\bFIXED\b", ln))
    other = len(status) - fixed
    headers = [
        ln
        for ln in lines
        if re.match(r"^\[[CHML]\]", ln) or re.match(r"^\[[A-Z]\]", ln)
    ]
    annexes = [ln for ln in lines if ln.startswith("## ANNEX")]
    return [
        f"recorded findings with a STATUS line: {len(status)} "
        f"({fixed} FIXED, {other} other)",
        f"severity-tagged finding headers: {len(headers)}",
        f"annexes: {len(annexes)} (latest: {annexes[-1] if annexes else 'none'})",
    ]


def report(root: Path, client_root: Path, findings: Path) -> tuple[str, int, int]:
    hits = sweep_core(root) + sweep_api(root) + sweep_client(client_root)
    grants = [h for h in hits if h.kind == GRANT]
    reviews = [h for h in hits if h.kind == REVIEW]
    non_auth = [h for h in hits if h.kind == NON_AUTH]

    facade_hits = sweep_facades(root)
    grants = grants + [h for h in facade_hits if h.kind == GRANT]
    reviews = reviews + [h for h in facade_hits if h.kind == REVIEW]
    non_auth = non_auth + [h for h in facade_hits if h.kind == NON_AUTH]

    fabricated = sweep_sdp_fabrication(root)

    undelivered, callbacks_scanned = sweep_callback_delivery(root, client_root)

    manager_hits = sweep_managers(root)
    unchecked = [h for h in manager_hits if h.kind == UNCHECKED]
    non_auth = non_auth + [h for h in manager_hits if h.kind == NON_AUTH]

    empty_decided = sweep_empty_decided(root)

    route_hits = sweep_routes(root) + sweep_ws_handlers(root)
    no_auth = [h for h in route_hits if h.kind == NO_AUTH]
    unauthorized = [h for h in route_hits if h.kind == UNAUTHZ]
    public = [h for h in route_hits if h.kind == PUBLIC]
    reviews = reviews + [h for h in route_hits if h.kind == REVIEW]

    out: list[str] = []
    out.append("# Fresh fail-open authorization inventory")
    out.append("")
    out.append(
        "Generated by `plexichat/ci-tooling/audit_failopen.py`. Regenerate with:"
    )
    out.append("")
    out.append("```bash")
    out.append("python ci-tooling/audit_failopen.py --findings ../findings.md")
    out.append("```")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append(f"- GRANT (fail open, must fix): {len(grants)}")
    out.append(
        f"- FABRICATED (SDP answer with invented ICE/DTLS credentials): "
        f"{len(fabricated)}"
    )
    out.append(
        f"- UNDELIVERED (client media callback no handler reads): "
        f"{len(undelivered)}"
        + ("" if callbacks_scanned else " (sweep H skipped: client tree not found)")
    )
    out.append(
        f"- EMPTY-DECIDED (access decided from a fetched listing): {len(empty_decided)}"
    )
    out.append(f"- REVIEW (needs a human call): {len(reviews)}")
    out.append(f"- NON-AUTH (verified denying/plumbing): {len(non_auth)}")
    out.append(f"- UNCHECKED (manager write ignoring its caller id): {len(unchecked)}")
    out.append(f"- NO-AUTH (route/handler with no authentication): {len(no_auth)}")
    out.append(
        f"- UNAUTHZ (authenticated, no visible authorization): {len(unauthorized)}"
    )
    out.append(f"- PUBLIC (verified by design): {len(public)}")
    out.append("")
    for line in findings_inventory(findings):
        out.append(f"- {line}")
    out.append("")

    for title, group in (
        ("GRANT - fails open", grants),
        (
            "FABRICATED - SDP answer with invented ICE/DTLS credentials",
            fabricated,
        ),
        (
            "UNDELIVERED - client media callback that no handler reads",
            undelivered,
        ),
        (
            "EMPTY-DECIDED - access decided from a listing the function fetched",
            empty_decided,
        ),
        ("REVIEW - needs a human call", reviews),
        ("NON-AUTH - verified fine", non_auth),
        (
            "UNCHECKED - manager write method that ignores its caller id",
            unchecked,
        ),
        (
            "NO-AUTH - route/handler with no authentication dependency",
            no_auth,
        ),
        (
            "UNAUTHZ - authenticated with no visible authorization call",
            unauthorized,
        ),
        ("PUBLIC - verified public or credentialed by design", public),
    ):
        out.append(f"## {title} ({len(group)})")
        out.append("")
        if not group:
            out.append("_(none)_")
            out.append("")
            continue
        out.append("| file:line | symbol | detail |")
        out.append("| --- | --- | --- |")
        for hit in group:
            out.append(f"| `{hit.path}:{hit.line}` | `{hit.symbol}` | {hit.detail} |")
        out.append("")

    fail_open = len(grants) + len(fabricated) + len(empty_decided)
    verdict_missing = len(no_auth) + len(unchecked) + len(undelivered)
    return "\n".join(out), fail_open, verdict_missing


def main(argv: list[str] | None = None) -> int:
    # NOTE: sweep H is reported as skipped (not as a pass) when the sibling
    # client checkout is absent, so a run without it cannot silently lose the
    # callback-contract coverage.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="plexichat server root")
    parser.add_argument(
        "--client-root",
        default="../plexichat-client",
        help="plexichat-client root (for sweeps C and H)",
    )
    parser.add_argument(
        "--findings",
        default="../findings.md",
        help="findings.md path for the recorded-inventory summary",
    )
    parser.add_argument(
        "--output", default="-", help="write the report here instead of stdout"
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    client_root = (root / args.client_root).resolve()
    findings = (root / args.findings).resolve()

    text, grant_count, no_auth_count = report(root, client_root, findings)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(
            f"wrote {args.output} ({grant_count} GRANT/FABRICATED/EMPTY-DECIDED, "
            f"{no_auth_count} NO-AUTH/UNCHECKED/UNDELIVERED hits)"
        )
    return 1 if (grant_count or no_auth_count) else 0


if __name__ == "__main__":
    sys.exit(main())
