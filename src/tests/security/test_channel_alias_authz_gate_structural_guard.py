"""Structural guard: channel-id-alias reverse lookups in authorization gates.

Background
----------
Message/channel APIs accept two spellings for the same server channel: its
``srv_channels.id`` and the linked ``srv_channels.conversation_id`` (the
"alias"). ``ParticipantService.is_participant`` grants every server member
participation under BOTH spellings (``srv_channels WHERE conversation_id = ?
OR id = ?``), but the membership-agnostic reverse resolver
``get_channel_by_conversation`` only matches ``conversation_id = ?``. So a
gate that reverse-looks-up a caller-supplied id and treats a miss as "this is
a genuine DM -> allow/skip" is bypassable: pass the channel's own ``id`` and
the resolver misses, the gate silently skips, and the caller gets a permission
decision they should never have received.

This exact bug was fixed in several places (scheduled ``e353bfb9``, reactions
``9c70df4f``, send/edit/slowmode/pin/bookmark/poll). Runtime tests cover each
site individually, but nothing stops a later edit from replacing a dual-form
lookup with a conversation-id-only one, or from adding a brand new gate with
the same miss-as-allow shape. This is a pure AST ratchet (no app import/config
/DB) with four halves:

1. ``_reverse_lookup_gates`` is a generic detector. It finds every production
   function holding a reverse lookup -- raw SQL ``srv_channels ... WHERE
   conversation_id = ?`` (including the dual ``OR id = ?`` form) or a
   ``get_channel_by_conversation`` call (direct or via a local alias) -- and
   whose *result* is tested on a miss with a bare ``return`` / ``return True``
   (i.e. the gate is skipped instead of denying). It also records the exact
   result variable so a rename does not fool it.

2. ``GATE_REGISTRY`` enumerates every such gate in the committed tree. Each
   entry states how both id forms are resolved (``dual-sql`` / ``paired`` /
   ``authoritative`` / ``central`` -- the central ``get_channel_by_conversation``
   resolver now resolves both forms and prefers the authoritative link) or is
   recorded as a ``finding`` with the evidence of the unfixed bypass and a
   reason. ``NON_AUTHZ_SITES`` documents the lookups that are NOT gates
   (notification fan-out, search indexing, erasure/cleanup, report context,
   the resolver helpers themselves); those are allowed to be single-form but
   may not hide a miss-as-allow branch.

3. The whole-tree sweep fails closed: a NEW miss-as-allow reverse-lookup gate
   with no registry entry raises. Removing the fix markers for a registered
   gate also raises. The registry must stay exactly in sync with the detector
   output, so an unknown gate can never be silently accepted.

4. Self-tests prove the detector is non-vacuous (a synthetic miss-as-allow
   gate is flagged; dual-form and documented sites are accepted; an allowlist
   entry suppresses only its own site), and that blobs really come from
   committed HEAD (``git show HEAD:<path>``), not the working tree.

Run with::

    pytest -n 0 -q src/tests/security/test_channel_alias_authz_gate_structural_guard.py
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Git / committed-tree plumbing (HEAD blobs only -- many source files are
# contested by parallel agents; a working-tree scan could see a stale copy or
# a whole-file commit could silently revert a peer's fix).
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
# Detector vocabulary
# ---------------------------------------------------------------------------

# The single-form reverse resolver and the id-form probes used to cover the
# other spelling. ``OR id = ?`` is the in-SQL fix; the probes are the
# "both lookups" fix (channel_exists / get_channel_conversation_id) or the
# paired channel-context resolution used by the send path.
DUAL_SQL_MARKER = "OR id = ?"
CENTRAL_RESOLVER_PATH = "src/core/servers/manager/base.py"
CENTRAL_RESOLVER_FUNC = "get_channel_by_conversation"
# The exact-match-first tie-break: a genuine conversation id must win a
# collision with an unrelated channel whose numeric id equals the value.
CENTRAL_PREFERENCE_MARKER = "(conversation_id = ?) DESC"
ID_FORM_PROBES = (
    "channel_exists",
    "get_channel_conversation_id",
    "_channel_ctx",
    "channel_ctx",
    "_resolve_channel_scope",
)


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _token_in(name: str, text: str) -> bool:
    """Substring with identifier boundaries (no regex -- avoids ``\\b`` bugs)."""
    start = 0
    while True:
        idx = text.find(name, start)
        if idx < 0:
            return False
        before = idx == 0 or not (text[idx - 1].isalnum() or text[idx - 1] == "_")
        end = idx + len(name)
        after = end == len(text) or not (text[end].isalnum() or text[end] == "_")
        if before and after:
            return True
        start = idx + 1


def _is_reverse_sql(node: ast.AST) -> bool:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    sql = node.value.lower()
    return (
        "srv_channels" in sql
        and "where" in sql
        and "conversation_id = ?" in sql
    )


def _has_reverse_sql(fn: ast.AST) -> bool:
    return any(_is_reverse_sql(node) for node in ast.walk(fn))


def _lookup_alias_names(fn: ast.AST) -> set[str]:
    """Local names bound to ``get_channel_by_conversation`` (via getattr or attr)."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        via_getattr = (
            _call_name(call) == "getattr"
            and len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
            and call.args[1].value == "get_channel_by_conversation"
        )
        via_attr = (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "get_channel_by_conversation"
        )
        via_name = _call_name(call) == "get_channel_by_conversation"
        if via_getattr or via_attr or via_name:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _result_variables(fn: ast.AST, aliases: set[str], raw_sql: bool) -> set[str]:
    """Variables assigned the *result* of a reverse lookup call."""
    results: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        name = _call_name(node.value)
        if name in aliases or (raw_sql and name in ("fetch_one", "fetchone")):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    results.add(target.id)
    return results


def _is_miss_allow_branch(node: ast.If, results: set[str]) -> bool:
    """True when ``if <result> is None / not <result>:`` skips the gate."""
    test = ast.unparse(node.test)
    if not any(_token_in(name, test) for name in results):
        return False
    miss = "is None" in test or any("not " + name in test for name in results)
    if not miss:
        return False
    for stmt in node.body:
        text = ast.unparse(stmt).strip()
        if text in ("return", "return True", "pass") or text.startswith("return True "):
            return True
    return False


@dataclass(frozen=True)
class Gate:
    path: str
    func: str
    lineno: int

    def key(self) -> tuple[str, str]:
        return (self.path, self.func)

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno} in {self.func}()"


def _reverse_lookup_gates(fn: ast.AST, path: str) -> list[Gate]:
    raw = _has_reverse_sql(fn)
    aliases = _lookup_alias_names(fn)
    if not (raw or aliases):
        return []
    results = _result_variables(fn, aliases, raw)
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and _is_miss_allow_branch(node, results):
            return [Gate(path, fn.name, node.lineno)]
    return []


@lru_cache(maxsize=None)
def _sweep(commit: str) -> tuple[Gate, ...]:
    """Enumerate every miss-as-allow reverse-lookup gate in committed HEAD."""
    reader = _make_blob_reader(commit)
    gates: list[Gate] = []
    for path in _production_files(commit):
        source = reader(path)
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise AssertionError(f"cannot parse committed {path}: {exc}") from exc
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                gates.extend(_reverse_lookup_gates(fn, path))
    return tuple(gates)


# ---------------------------------------------------------------------------
# Registry. ``resolution`` is one of:
#   * "dual-sql"  -- the reverse SQL itself matches ``OR id = ?``.
#   * "paired"    -- a second id-form lookup (channel_exists /
#                    get_channel_conversation_id / channel context) covers the
#                    channel-id spelling.
#   * "authoritative" -- the conversation id is read from a stored message row
#                    (cannot be substituted with a channel id by the caller).
#   * "central"    -- the gate delegates to the central
#                    ``get_channel_by_conversation`` resolver, which now
#                    resolves ``conversation_id`` OR ``id`` and prefers the
#                    authoritative link, so the miss-as-allow branch can no
#                    longer be reached with a channel-id alias.
#   * "finding"    -- KNOWN unfixed bypass: caller-supplied id, miss-as-allow,
#                    no id-form probe. These are reported, not declared safe.
# ``evidence`` markers must remain in the module for non-finding entries; for
# findings, the detector must keep flagging them (so they stay visible).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateEntry:
    label: str
    path: str
    func: str
    resolution: str
    evidence: tuple[str, ...]
    why: str

    def key(self) -> tuple[str, str]:
        return (self.path, self.func)


GATE_REGISTRY: tuple[GateEntry, ...] = (
    GateEntry(
        "scheduled-message post gate",
        "src/core/messaging/services/scheduled.py",
        "_assert_channel_post_access",
        "dual-sql",
        (DUAL_SQL_MARKER, "srv_channels"),
        "e353bfb9: the reverse SQL matches both id forms so a channel-id "
        "alias cannot make this gate skip; a miss denies.",
    ),
    GateEntry(
        "alias send gate",
        "src/api/routes/messages/messages_crud/base.py",
        "_require_alias_send_access",
        "paired",
        ("get_channel(", "messages.send"),
        "Called only from the DM-style fallback when get_channel(channel_id) "
        "returned no channel: the channel-id spelling is gated by the "
        "messages.send block in _send_message_with_fallback, the linked "
        "conversation_id spelling by this reverse lookup.",
    ),
    GateEntry(
        "alias automod gate",
        "src/api/routes/messages/messages_crud/base.py",
        "_enforce_alias_automod",
        "paired",
        ("_channel_ctx", "get_channel_by_conversation"),
        "Uses the channel context resolved from the URL id for the channel-id "
        "spelling and the reverse lookup for the conversation-id alias.",
    ),
    GateEntry(
        "message retrieve view gate",
        "src/api/routes/messages/messages_crud/retrieve.py",
        "_require_channel_view",
        "paired",
        ("channel_exists", "get_channel_conversation_id"),
        "Probes channel_exists/get_channel_conversation_id for the channel-id "
        "spelling before trusting the conversation-id reverse lookup.",
    ),
    GateEntry(
        "pin/unpin message view gate",
        "src/api/routes/messages/pins.py",
        "_require_message_channel_view",
        "paired",
        ("channel_exists", "_resolve_channel_scope"),
        "The URL id is gated by _resolve_channel_scope/channel_exists (id "
        "spelling); the reverse lookup uses the authoritative message row's "
        "conversation id for the alias spelling.",
    ),
    GateEntry(
        "read-receipt view gate",
        "src/api/routes/messages/read_receipts.py",
        "_require_read_receipt_channel_view",
        "paired",
        ("channel_exists",),
        "channel_exists covers the channel-id spelling; the reverse lookup "
        "covers the linked conversation-id spelling.",
    ),
    GateEntry(
        "attachment upload send gate",
        "src/api/routes/channels/attachments.py",
        "_require_attachment_send_access",
        "paired",
        ("channel_exists", "get_channel("),
        "Invoked only when the route's get_channel(cid) path found no channel; "
        "the channel-id spelling is gated there, the alias spelling here.",
    ),
    GateEntry(
        "message edit/delete view gate",
        "src/core/messaging/services/message/edit_delete.py",
        "_can_view_conversation_messages",
        "paired",
        ("get_channel_conversation_id", "get_channel("),
        "Module-level _resolve_channel_scope uses get_channel_conversation_id "
        "for the id spelling; the reverse lookup covers the alias spelling. "
        "d067dccc also derives authoritative server scope for edits.",
    ),
    GateEntry(
        "edit-history view gate",
        "src/core/messaging/services/edit_history.py",
        "_require_channel_view",
        "authoritative",
        ("msg_row", "conversation_id"),
        "The conversation id is read from the stored message row "
        "(msg_row['conversation_id']), not the caller's URL id, so the "
        "reverse lookup always sees the authoritative linked id.",
    ),
    GateEntry(
        "poll post gate",
        "src/core/polls/manager/post_gate.py",
        "can_post_poll_message",
        "paired",
        ("channel_exists", "get_channel_conversation_id"),
        "On a reverse miss it probes channel_exists / "
        "get_channel_conversation_id and applies messages.send when the id is "
        "actually a server channel.",
    ),
    GateEntry(
        "poll read gate",
        "src/core/polls/manager/reading.py",
        "_can_view_poll_message",
        "paired",
        ("channel_exists", "get_channel_conversation_id"),
        "Same dual-lookup handling as the poll post gate for the channel-id "
        "spelling.",
    ),
    # --- N735 gates, fixed centrally. Each still has the miss-as-allow shape
    #     (so the detector enumerates it), but the resolver they all delegate
    #     to now resolves both id forms and prefers the authoritative link,
    #     closing the channel-id-alias bypass for all four at once.
    GateEntry(
        "forward target send gate (central-resolver fixed)",
        "src/api/routes/features/forwarding.py",
        "_require_target_send_access",
        "central",
        ("get_channel_by_conversation",),
        "target_conversation_id is caller-supplied. is_participant grants via "
        "srv_channels.id; the central get_channel_by_conversation now matches "
        "conversation_id OR id (conversation-first), so a channel-id alias can "
        "no longer miss and skip the messages.send gate.",
    ),
    GateEntry(
        "forwarding service view gate (central-resolver fixed)",
        "src/core/messaging/services/forwarding.py",
        "_require_channel_view",
        "central",
        ("get_channel_by_conversation",),
        "Called with the caller-supplied target_conversation_id; the central "
        "dual-form resolver now catches the channel-id spelling before the "
        "miss-as-allow branch can skip the channels.view gate.",
    ),
    GateEntry(
        "voice send gate (central-resolver fixed)",
        "src/api/routes/features/voice.py",
        "_require_voice_send_access",
        "central",
        ("get_channel_by_conversation",),
        "conv_id comes from body.conversation_id; the central dual-form "
        "resolver now catches a channel-id alias, so view/send/timeout gates "
        "cannot be skipped before messaging.send_message.",
    ),
    GateEntry(
        "artifact inline-message send gate (central-resolver fixed)",
        "src/api/routes/artifacts_helpers.py",
        "_can_emit_artifact_message",
        "central",
        ("get_channel_by_conversation",),
        "artifact.conversation_id originates from body.conversation_id; the "
        "central dual-form resolver now catches a channel-id alias, so the "
        "messages.send/view/timeout gates cannot be skipped.",
    ),
    # Pin/unpin service-layer view gate. HEAD still carries the miss-as-allow
    # branch (``info is None -> True``), but the lookup delegates to the same
    # central dual-form resolver fixed in 3cc3ba7b, so a channel-id alias can
    # no longer miss that branch: ``conversation_id OR id`` (conversation-first)
    # resolves it to the backing channel and the channels.view check runs.
    # A working tree is free to drop this helper; the entry documents the gate
    # while it remains in the committed tree.
    GateEntry(
        "pin service view gate (central-resolver fixed)",
        "src/core/messaging/services/pin.py",
        "_can_view_conversation_messages",
        "central",
        ("get_channel_by_conversation",),
        "conversation_id is caller-supplied at the service layer. The gate "
        "delegates to get_channel_by_conversation, which now matches "
        "conversation_id OR id (conversation-first), so a channel-id alias "
        "cannot miss and skip the channels.view grant check.",
    ),
)


# Non-authorization reverse lookups. They may be single-form because a miss
# does not grant access; each needs a reason proving it is not a gate.
@dataclass(frozen=True)
class NonAuthzEntry:
    label: str
    path: str
    func: str
    why: str

    def key(self) -> tuple[str, str]:
        return (self.path, self.func)


NON_AUTHZ_SITES: tuple[NonAuthzEntry, ...] = (
    NonAuthzEntry(
        "notification fan-out",
        "src/core/messaging/services/message/send.py",
        "_dispatch_message_notifications",
        "Resolves channel metadata to decide notification routing; no access "
        "decision -- a miss only suppresses a notification.",
    ),
    NonAuthzEntry(
        "notification fan-out job",
        "src/core/messaging/services/message/send.py",
        "_fanout_job",
        "Background delivery metadata; no allow/deny decision.",
    ),
    NonAuthzEntry(
        "search index projection",
        "src/core/search/manager/message_indexing.py",
        "index_message",
        "Joins channel metadata for index documents; read access is enforced "
        "elsewhere by the search ACL layer.",
    ),
    NonAuthzEntry(
        "erasure live-channel check",
        "src/core/erasure.py",
        "_is_live_server_channel_conversation",
        "Cleanup/retention metadata: determines whether to purge rows; a miss "
        "never grants access.",
    ),
    NonAuthzEntry(
        "automod alert metadata",
        "src/core/automod/actions/alert.py",
        "_send_channel_alert",
        "Resolves alert destination metadata; no requester authorization.",
    ),
    NonAuthzEntry(
        "reaction backing-channel resolver (dual-form helper)",
        "src/core/reactions/manager/permissions.py",
        "_get_channel_for_conversation",
        "9c70df4f already resolves conversation_id OR id; it returns a row and "
        "makes no allow/deny decision -- the reaction permission gates do.",
    ),
    NonAuthzEntry(
        "embed channel helper",
        "src/core/embeds/manager/base.py",
        "_get_channel_for_conversation",
        "Returns a row to the caller; the caller"
        " (_check_embed_links_permission) makes the permission decision with "
        "has_permission, not this lookup.",
    ),
    NonAuthzEntry(
        "bookmark ownership metadata",
        "src/core/messaging/services/bookmarks.py",
        "_channel_owns_conversation",
        "Membership-agnostic metadata used by the read gate, not a gate "
        "itself; the paired dual-form probes live in _conversation_read_allowed.",
    ),
    NonAuthzEntry(
        "report context",
        "src/api/routes/reports.py",
        "_report_context_for_conversation",
        "Builds report metadata after the caller was authorized; a miss does "
        "not skip a permission gate.",
    ),
    NonAuthzEntry(
        "feature report context",
        "src/api/routes/features/reports.py",
        "_message_report_context",
        "Same: report payload metadata, not an authorization gate.",
    ),
    NonAuthzEntry(
        "central reverse resolver",
        CENTRAL_RESOLVER_PATH,
        CENTRAL_RESOLVER_FUNC,
        "The central resolver itself; it returns a row or None and makes no "
        "allow/deny choice. It resolves conversation_id OR id and prefers the "
        "authoritative link, so gates delegating to it get both id forms; the "
        "per-gate resolutions are recorded in GATE_REGISTRY.",
    ),
    NonAuthzEntry(
        "public reverse resolver",
        "src/core/servers/channels.py",
        "get_channel_by_conversation",
        "Thin public wrapper around the central resolver; no gate decision.",
    ),
    NonAuthzEntry(
        "manager-mixin reverse resolver",
        "src/core/servers/channel_mixin.py",
        "get_channel_by_conversation",
        "Manager mixin wrapper; no gate decision.",
    ),
)


def _registry_keys() -> set[tuple[str, str]]:
    return {entry.key() for entry in GATE_REGISTRY}


def _non_authz_keys() -> set[tuple[str, str]]:
    return {(e.path, e.func) for e in NON_AUTHZ_SITES}


def _assert_entry_evidence(entry: GateEntry, source: str) -> None:
    body = source
    if entry.resolution == "finding":
        # A finding must stay a finding: still single-form, no id-form probe
        # snuck in unnoticed, else it should be promoted in the registry.
        assert DUAL_SQL_MARKER not in body, (
            f"{entry.label}: finding now has dual SQL; promote it to dual-sql"
        )
        for probe in ID_FORM_PROBES:
            assert probe not in body, (
                f"{entry.label}: finding gained id-form probe {probe!r}; "
                "reclassify it in GATE_REGISTRY"
            )
        return
    if entry.resolution == "central":
        # The gate must still delegate to the central resolver (not shadow it
        # with its own single-form SQL). The resolver's dual-form SQL is
        # verified against the committed blob by the dedicated test below, so
        # reverting the central fix fails the guard for every 'central' gate.
        assert "get_channel_by_conversation" in body, (
            f"{entry.label}: central-fixed gate no longer delegates to "
            "get_channel_by_conversation"
        )
        return
    assert entry.resolution in {
        "dual-sql",
        "paired",
        "authoritative",
        "central",
    }, entry.resolution
    for marker in entry.evidence:
        assert marker in body, (
            f"{entry.label}: evidence marker {marker!r} missing from {entry.path}; "
            "the both-id-forms resolution may have been removed"
        )
    if entry.resolution == "dual-sql":
        assert DUAL_SQL_MARKER in body, f"{entry.label}: dual SQL marker removed"
    elif entry.resolution == "paired":
        assert any(probe in body for probe in ID_FORM_PROBES), (
            f"{entry.label}: no id-form probe left in the module"
        )


# ---------------------------------------------------------------------------
# Self-tests: a detector that never fires must not let the real scan pass.
# ---------------------------------------------------------------------------


def _parse(src: str) -> ast.AST:
    return ast.parse(src)


def test_self_test_synthetic_miss_as_allow_gate_is_flagged():
    src = """
def gate(conversation_id, user_id, servers):
    lookup = getattr(servers, "get_channel_by_conversation", None)
    info = lookup(conversation_id)
    if info is None:
        return  # skip the gate -> bypassable with a channel-id alias
    return bool(servers.has_permission(user_id, info["server_id"]))
"""
    fn = [n for n in ast.walk(_parse(src)) if isinstance(n, ast.FunctionDef)][0]
    gates = _reverse_lookup_gates(fn, "src/api/_synthetic.py")
    assert len(gates) == 1, gates
    assert gates[0].func == "gate"


def test_self_test_synthetic_raw_sql_miss_as_allow_gate_is_flagged():
    src = """
def gate(db, conversation_id):
    row = db.fetch_one(
        "SELECT id FROM srv_channels WHERE conversation_id = ?",
        (conversation_id,),
    )
    if row is None:
        return True
    return False
"""
    fn = [n for n in ast.walk(_parse(src)) if isinstance(n, ast.FunctionDef)][0]
    assert len(_reverse_lookup_gates(fn, "src/api/_synthetic.py")) == 1


def test_self_test_dual_form_gates_are_accepted():
    dual_sql = """
def gate(db, conversation_id):
    row = db.fetch_one(
        "SELECT id FROM srv_channels WHERE conversation_id = ? OR id = ?",
        (conversation_id, conversation_id),
    )
    if row is None:
        return True
    return False
"""
    dual_probe = """
def gate(servers, conversation_id, user_id):
    lookup = getattr(servers, "get_channel_by_conversation", None)
    info = lookup(conversation_id)
    if info is None:
        if servers.channel_exists(conversation_id):
            return False
        return True
    return False
"""
    for src in (dual_sql, dual_probe):
        fn = [n for n in ast.walk(_parse(src)) if isinstance(n, ast.FunctionDef)][0]
        # The detector still sees the gate (that is by design); resolution is
        # proven by the dual-form evidence markers, not by hiding the gate.
        gates = _reverse_lookup_gates(fn, "src/api/_synthetic.py")
        assert gates, "detector must still enumerate the dual-form gate"
        assert DUAL_SQL_MARKER in src or any(
            p in src for p in ID_FORM_PROBES
        ), "test source lost its dual-form evidence"


def test_self_test_miss_as_deny_is_not_a_gate():
    src = """
def gate(db, conversation_id):
    row = db.fetch_one(
        "SELECT id FROM srv_channels WHERE conversation_id = ?",
        (conversation_id,),
    )
    if row is None:
        raise PermissionError("cannot verify channel")
    return True
"""
    fn = [n for n in ast.walk(_parse(src)) if isinstance(n, ast.FunctionDef)][0]
    assert _reverse_lookup_gates(fn, "src/api/_synthetic.py") == []


def test_self_test_renamed_result_variable_is_still_detected():
    src = """
def gate(servers, conversation_id, user_id):
    resolve = getattr(servers, "get_channel_by_conversation", None)
    backing = resolve(conversation_id)
    if backing is None:
        return  # skip
    return True
"""
    fn = [n for n in ast.walk(_parse(src)) if isinstance(n, ast.FunctionDef)][0]
    assert len(_reverse_lookup_gates(fn, "src/api/_synthetic.py")) == 1


def test_self_test_allowlist_does_not_hide_a_new_gate():
    src = """
def brand_new_gate(servers, conversation_id, user_id):
    lookup = getattr(servers, "get_channel_by_conversation", None)
    info = lookup(conversation_id)
    if info is None:
        return True
    return False
"""
    fn = [n for n in ast.walk(_parse(src)) if isinstance(n, ast.FunctionDef)][0]
    registered = _non_authz_keys() | _registry_keys()
    assert ("src/api/_synthetic.py", "brand_new_gate") not in registered
    gates = _reverse_lookup_gates(fn, "src/api/_synthetic.py")
    offenders = [g for g in gates if g.key() not in registered]
    assert len(offenders) == 1, offenders


def test_self_test_documented_gate_is_accepted_but_a_sibling_is_not():
    src = """
def documented_gate(servers, conversation_id, user_id):
    lookup = getattr(servers, "get_channel_by_conversation", None)
    info = lookup(conversation_id)
    if info is None:
        return True
    return False


def brand_new_gate(servers, conversation_id, user_id):
    lookup = getattr(servers, "get_channel_by_conversation", None)
    info = lookup(conversation_id)
    if info is None:
        return True
    return False
"""
    tree = _parse(src)
    path = "src/api/_synthetic.py"
    gates = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            gates.extend(_reverse_lookup_gates(fn, path))
    assert {g.func for g in gates} == {"documented_gate", "brand_new_gate"}
    documented = {(path, "documented_gate")}
    offenders = [g for g in gates if g.key() not in documented]
    assert [g.func for g in offenders] == ["brand_new_gate"]


def test_self_test_allowlist_may_not_hide_a_real_gate():
    # Even if a miss-as-allow gate were accidentally added to the non-authz
    # allowlist, the sweep still enumerates it: the allowlist only suppresses
    # *reporting*, and the sync test below refuses a mismatch.
    src = """
def metadata_resolver(servers, conversation_id):
    lookup = getattr(servers, "get_channel_by_conversation", None)
    info = lookup(conversation_id)
    if info is None:
        return True
    return False
"""
    fn = [n for n in ast.walk(_parse(src)) if isinstance(n, ast.FunctionDef)][0]
    gates = _reverse_lookup_gates(fn, "src/api/_synthetic.py")
    assert len(gates) == 1
    # Allowlist membership never changes the detector's output: the sync test
    # (test_enumerated_gates_match_registry_exactly) is what rejects a gate
    # that is hiding in the non-authz allowlist.
    assert gates[0].key() not in _non_authz_keys()


def test_registry_entries_require_resolution_and_reason():
    assert GATE_REGISTRY, "no gates registered"
    assert NON_AUTHZ_SITES, "no non-authz sites documented"
    for entry in GATE_REGISTRY:
        assert entry.resolution in {
            "dual-sql",
            "paired",
            "authoritative",
            "central",
            "finding",
        }
        assert entry.why.strip(), f"{entry.label}: empty reason"
        assert entry.path.startswith("src/"), entry.path
        assert entry.func, entry.label
    for entry in NON_AUTHZ_SITES:
        assert entry.why.strip(), f"{entry.label}: empty reason"
        assert entry.path.startswith("src/"), entry.path
    assert len(_registry_keys()) == len(GATE_REGISTRY), "duplicate gate entries"
    assert len(_non_authz_keys()) == len(NON_AUTHZ_SITES), "duplicate non-authz entries"


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    files = _production_files(commit_sha)
    assert len(files) >= 1000, f"only {len(files)} production modules scanned"
    for required in (
        "src/core/messaging/services/scheduled.py",
        "src/core/reactions/manager/permissions.py",
        "src/api/routes/messages/messages_crud/base.py",
        "src/core/polls/manager/post_gate.py",
        "src/api/routes/features/forwarding.py",
        "src/api/routes/features/voice.py",
        "src/core/servers/manager/base.py",
    ):
        assert required in files, f"{required} unexpectedly excluded from scan"
    reader = _make_blob_reader(commit_sha)
    # HEAD blobs, not the (contested) working tree: the dual-form fixes must
    # really be present in the committed revision.
    assert DUAL_SQL_MARKER in reader("src/core/messaging/services/scheduled.py")
    assert DUAL_SQL_MARKER in reader("src/core/reactions/manager/permissions.py")


def test_enumerated_gates_match_registry_exactly(commit_sha):
    found = {g.key() for g in _sweep(commit_sha)}
    expected = _registry_keys()
    assert found == expected, (
        "channel-id-alias gate enumeration drifted from the registry.\n"
        f"  undocumented (NEW) gates: {sorted(found - expected)}\n"
        f"  stale registry entries:   {sorted(expected - found)}"
    )


def test_registered_gates_keep_dual_form_or_documented_finding(commit_sha):
    reader = _make_blob_reader(commit_sha)
    for entry in GATE_REGISTRY:
        _assert_entry_evidence(entry, reader(entry.path))


def test_non_authz_sites_are_real_and_not_miss_allow_gates(commit_sha):
    reader = _make_blob_reader(commit_sha)
    found = {g.key() for g in _sweep(commit_sha)}
    for entry in NON_AUTHZ_SITES:
        source = reader(entry.path)
        assert "get_channel_by_conversation" in source or "srv_channels" in source, (
            f"{entry.label}: documented site no longer contains a reverse lookup"
        )
        assert entry.key() not in found, (
            f"{entry.label}: documented non-authz site is actually a "
            "miss-as-allow gate -- promote it to GATE_REGISTRY"
        )


def test_no_new_undocumented_authz_gate_in_committed_tree(commit_sha):
    """Fail closed: any new miss-as-allow reverse lookup must be documented."""
    offenders = [g for g in _sweep(commit_sha) if g.key() not in _registry_keys()]
    # NON_AUTHZ_SITES may never suppress a real gate either.
    offenders = [g for g in offenders if g.key() not in _non_authz_keys()]
    assert not offenders, (
        "New conversation-id-only authorization gate(s) that skip on a miss "
        "(resolve both id forms or document them in GATE_REGISTRY):\n  "
        + "\n  ".join(str(g) for g in offenders)
    )


def test_central_resolver_resolves_both_id_forms(commit_sha):
    """The committed central resolver must resolve both forms, link first.

    This is what makes every ``central`` registry entry non-vacuous: reverting
    the resolver to ``WHERE conversation_id = ?`` fails this test even though
    the four gates' own source is unchanged.
    """
    source = _make_blob_reader(commit_sha)(CENTRAL_RESOLVER_PATH)
    assert DUAL_SQL_MARKER in source, "central resolver lost its id-form branch"
    assert CENTRAL_PREFERENCE_MARKER in source, (
        "central resolver lost the exact-conversation-id-first tie-break; a "
        "genuine conversation id could resolve to the wrong channel on collision"
    )


def test_prior_findings_are_now_central_fixed(commit_sha):
    """The four N735 gates stay promoted to central-fixed.

    Other gates may also be fixed via the central resolver (e.g. the pin
    service view gate), so assert the required four are present rather than
    pinning the exact size of the central set.
    """
    central = [e for e in GATE_REGISTRY if e.resolution == "central"]
    required_central = {
        ("src/api/routes/features/forwarding.py", "_require_target_send_access"),
        ("src/core/messaging/services/forwarding.py", "_require_channel_view"),
        ("src/api/routes/features/voice.py", "_require_voice_send_access"),
        ("src/api/routes/artifacts_helpers.py", "_can_emit_artifact_message"),
    }
    assert required_central <= {e.key() for e in central}, (
        "the four centrally-fixed N735 gates were lost"
    )
    # No finding may remain disguised as fixed: every remaining finding must
    # still be a real, documented miss-as-allow bypass in committed HEAD.
    reader = _make_blob_reader(commit_sha)
    for entry in GATE_REGISTRY:
        if entry.resolution != "finding":
            continue
        assert entry.label.startswith("FINDING:")
        assert "contested" in entry.why.lower(), (
            f"{entry.label}: finding must record why it is reported, not patched"
        )
        source = reader(entry.path)
        assert "get_channel_by_conversation" in source
        assert DUAL_SQL_MARKER not in source


def test_pin_gate_central_evidence_is_non_vacuous(commit_sha):
    """Reverting the pin gate to single-form SQL must be flagged.

    A ``central`` entry is only meaningful if it rejects a silent revert to a
    conversation-id-only lookup. Revert the committed pin gate in memory to
    that shape and show both halves fail: the central evidence check rejects
    the missing delegation, and the detector still enumerates the single-form
    miss-as-allow gate (so an unregistered copy trips the whole-tree sweep).
    """
    entry = next(
        e
        for e in GATE_REGISTRY
        if e.key()
        == ("src/core/messaging/services/pin.py", "_can_view_conversation_messages")
    )
    assert entry.resolution == "central"
    source = _make_blob_reader(commit_sha)(entry.path)
    # The committed gate satisfies the central-fixed evidence check.
    _assert_entry_evidence(entry, source)

    old_block = (
        '    reverse = getattr(servers, "get_channel_by_conversation", None)\n'
        "    if not callable(reverse):\n"
        "        return True  # legacy/stub store: only the participation gate is available\n"
        "    try:\n"
        "        info = reverse(int(conversation_id))\n"
        "    except Exception:\n"
        "        return False  # unproven alias must never grant access\n"
    )
    new_block = (
        "    try:\n"
        "        info = servers._db.fetch_one(\n"
        '            "SELECT id FROM srv_channels WHERE conversation_id = ?",\n'
        "            (int(conversation_id),),\n"
        "        )\n"
        "    except Exception:\n"
        "        return False\n"
    )
    assert source.count(old_block) == 1, "pin gate central delegation not found"
    reverted = source.replace(old_block, new_block)
    assert "get_channel_by_conversation" not in reverted

    # The central marker is gone -> the registered gate fails evidence review.
    with pytest.raises(AssertionError, match="no longer delegates"):
        _assert_entry_evidence(entry, reverted)

    # The single-form lookup is independently detected as a miss-as-allow gate,
    # so an uncovered copy would be reported by the whole-tree sweep.
    tree = ast.parse(reverted)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == entry.func
    )
    gates = _reverse_lookup_gates(fn, entry.path)
    assert len(gates) == 1 and gates[0].key() == entry.key(), gates
