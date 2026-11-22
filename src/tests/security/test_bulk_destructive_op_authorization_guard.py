"""Structural guard: bulk/batch destructive manager ops cannot go ungated.

Follow-up to the bulk-op authorization campaign. A peer audited every
bulk/batch destructive manager operation and verified that each one is either
internally gated, inherently self-scoped to the caller's identity, reachable
only through a gated route, or harmless because it is read-only / cache-only /
an abstract stub. Runtime tests cover the individual call sites, but nothing
stopped a later edit from deleting one of those gates or from adding a brand
new ``purge_*`` / ``delete_*_bulk`` / ``*_all`` / ``*_for_channel`` operation
whose only route caller has no authorization check at all.

This is a pure AST ratchet (no app import/config/DB) over blobs read from
COMMITTED HEAD (``git show HEAD:<path>``), never the working tree -- many
production files are contested by parallel agents, and a stale working copy
could both mislead the scan and let a whole-file commit silently revert a fix.

Three halves:

1. ``BULK_OPS`` is a reviewed registry of *every* name-matching operation in
   ``src/core/**/manager*`` and ``src/core/**/services/**``. Each entry carries
   the evidence markers that must still appear in the committed function body
   plus a ``verdict`` explaining why it is safe (``gate`` / ``self_scoped`` /
   ``route_gated`` / ``allowlist``) and a non-empty reason. Removing a gate or
   changing a documented site fails even when no runtime fixture covers it.

2. A whole-scope sweep enumerates the name-matching operations in the committed
   tree and fails closed when one is not in the registry. For an unregistered
   operation, :func:`_assess` denies it unless it has an internal gate or every
   route caller is gated/self-scoped -- so a fresh ungated ``purge_all_X`` with
   an ungated route caller is rejected before anybody documents it.

3. Non-vacuity is proven in-memory: the caller detector flags a synthetic
   ungated ``purge_all_X`` route caller, accepts gated and self-scoped callers,
   the per-entry check fails when a gate marker is removed, and the committed
   sweep is shown to actually find the documented operations.
"""

from __future__ import annotations

import ast
import re
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
    @lru_cache(maxsize=None)
    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}")
        assert proc.returncode == 0, f"blob missing from {commit}: {path}"
        return proc.stdout

    return read


@lru_cache(maxsize=None)
def _tracked_files(commit: str, prefix: str) -> tuple[str, ...]:
    proc = _git("ls-tree", "-r", "--name-only", commit, "--", prefix)
    assert proc.returncode == 0, f"cannot list {prefix}: {proc.stderr.strip()}"
    return tuple(p for p in proc.stdout.splitlines() if p.endswith(".py"))


# ---------------------------------------------------------------------------
# Scope + naming vocabulary
# ---------------------------------------------------------------------------


# ``src/core/**/manager*`` and ``src/core/**/services/**``.
def _in_scope(path: str) -> bool:
    if not (path.startswith("src/core/") and path.endswith(".py")):
        return False
    parts = path.split("/")
    return any(p.startswith("manager") for p in parts) or "services" in parts


# The naming patterns named in the campaign.
BULK_NAME_RE = re.compile(
    r"(_bulk|_for_channel|_all)$|^(delete_many|purge_|clear_|remove_all_)"
)


def _is_bulk_name(name: str) -> bool:
    return bool(BULK_NAME_RE.search(name))


# An "authoritative gate" inside the operation body. These can raise/deny, so
# their continued presence proves the op cannot be driven by an unauthorized
# caller even if a route forgets its own check.
GATE_MARKERS: tuple[str, ...] = (
    "has_permission",
    "check_permission",
    "_check_server_permission",
    "require_permission",
    "require_admin_permission",
    "PermissionDeniedError",
    "ForbiddenError",
    "is_participant",
    "ParticipantRole.OWNER",
    "ParticipantRole.ADMIN",
    "get_participant",
    "owner_id",
)

# An identity-scoped predicate: the write only touches rows owned by the
# caller / explicitly supplied identity, so there is no cross-user blast radius.
SCOPE_MARKERS: tuple[str, ...] = (
    "WHERE user_id = ?",
    "user_id = ?",
    "owner_id = ?",
    "subject_id = ?",
    "conversation_id = ?",
    "except_session_id",
    "self.user_id",
)

# Markers that make a route/websocket caller "gated" or "self-scoped". An
# ungated caller that merely dispatches the operation carries none of these.
CALLER_GATE_MARKERS: tuple[str, ...] = (
    "require_admin_permission",
    "require_permission",
    "require_approved_request",
    "require_destructive_confirmation",
    "check_host_restriction",
    "require_snowflake",
    "parse_id",
    "_require_channel_view",
    "_require_read_receipt_channel_view",
    "current_user",
    "request.state.user",
    "client.user_id",
    "websocket.state",
    "has_permission",
    "check_permission",
    "PermissionDeniedError",
    "check_bot_owner",
    "bot_token",
    "credential",
)


# ---------------------------------------------------------------------------
# Registry: every documented operation and its verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BulkOp:
    label: str
    rel_path: str
    func: str
    verdict: str  # gate | self_scoped | route_gated | allowlist
    evidence: tuple[str, ...]  # substrings that must remain in the function body
    why: str


# Verdict semantics enforced by _assess():
#   gate        - body contains an authoritative GATE_MARKER
#   self_scoped - body contains a SCOPE_MARKER, or the signature is keyed on a
#                 caller identity and delegates to a downstream scoped writer
#   route_gated - no internal gate, but every route/WS caller is gated/scoped
#   allowlist   - reviewed safe for a documented reason (read-only, cache-only,
#                 abstract stub, or delegating to one of the entries above)
BULK_OPS: tuple[BulkOp, ...] = (
    # -- internal authoritative gate ----------------------------------------
    BulkOp(
        "message bulk-delete service",
        "src/core/messaging/services/message/edit_delete.py",
        "delete_messages_bulk",
        "gate",
        ("is_participant", "moderator"),
        "Deletes only messages the caller may delete (author/OWNER/ADMIN or "
        "messages.manage moderator) and only inside the requested conversation.",
    ),
    BulkOp(
        "message bulk-archive service",
        "src/core/messaging/services/message/edit_delete.py",
        "archive_messages_bulk",
        "gate",
        ("ParticipantRole.OWNER", "get_participant"),
        "Archiving requires an OWNER/ADMIN participant role on every message; "
        "non-owners are skipped, so the batch cannot widen one user's reach.",
    ),
    BulkOp(
        "bulk participant check",
        "src/core/messaging/services/participant.py",
        "are_participants_bulk",
        "gate",
        ("is_participant",),
        "Read-only batched participant resolution; the per-conversation check "
        "is authoritative and the result only annotates list responses.",
    ),
    BulkOp(
        "remove all reactions",
        "src/core/reactions/manager/reactions.py",
        "remove_all_reactions",
        "gate",
        ("_check_server_permission", "PermissionDeniedError"),
        "Requires messages.manage on the channel or conversation owner/admin; "
        "fails closed when a soft-deleted conversation cannot be verified.",
    ),
    BulkOp(
        "remove all reactions for emoji",
        "src/core/reactions/manager/reactions.py",
        "remove_all_reactions_for_emoji",
        "gate",
        ("_check_server_permission", "PermissionDeniedError"),
        "Same authoritative permission block as remove_all_reactions, with an "
        "emoji filter; no unauthenticated wipe path.",
    ),
    BulkOp(
        "bulk channel access resolution",
        "src/core/search/manager/access.py",
        "_can_access_channels_bulk",
        "gate",
        ("has_permission",),
        "Batched messages.read resolution; each channel is checked with the "
        "authoritative has_permission call, so search cannot widen scope.",
    ),
    BulkOp(
        "bulk permission resolution",
        "src/core/servers/manager/permission_ops.py",
        "get_permissions_bulk",
        "gate",
        ("owner_id",),
        "Read-only per-user permission batch; owner/admin checks gate any "
        "elevated result and the caller identity is bound into each query.",
    ),
    BulkOp(
        "purge one workbook",
        "src/core/suite/sum/manager_admin.py",
        "purge_workbook_by_id",
        "gate",
        ("owner_id",),
        "Irreversible workbook purge verifies ownership/authority before "
        "erasing blobs and metadata; reachable from the admin deletion flow.",
    ),
    BulkOp(
        "purge user workbook data",
        "src/core/suite/sum/manager_admin.py",
        "purge_user_data",
        "gate",
        ("owner_id", "DELETE FROM sum_grants WHERE subject_id = ?"),
        "Account-erasure hook gated by workbook ownership; every DELETE is "
        "bounded by the target user id and the outer-transaction guard fails "
        "closed before touching blob storage.",
    ),
    # -- self-scoped operations ---------------------------------------------
    BulkOp(
        "logout all sessions",
        "src/core/auth/managers/session/lifecycle.py",
        "logout_all",
        "self_scoped",
        ("WHERE user_id = ?",),
        "Revokes only rows owned by the single user_id argument; callers are "
        "the user's own logout, admin force-logout, or the deletion reaper.",
    ),
    BulkOp(
        "revoke sessions bulk",
        "src/core/auth/managers/session/lifecycle.py",
        "revoke_sessions_bulk",
        "self_scoped",
        ("WHERE user_id = ?", "except_session_id"),
        "The UPDATE is bounded by user_id so one user cannot revoke another's "
        "sessions; except_session_id keeps the current session alive.",
    ),
    BulkOp(
        "clear last chat",
        "src/core/messaging/services/last_chat.py",
        "clear_last_chat",
        "self_scoped",
        ("DELETE FROM user_last_chat WHERE user_id = ?",),
        "Deletes only the supplied user's own last-chat row and invalidates "
        "only that user's cache key; no cross-user delete.",
    ),
    BulkOp(
        "clear activity",
        "src/core/presence/manager/activity.py",
        "clear_activity",
        "self_scoped",
        ("DELETE FROM pres_activity WHERE user_id = ?",),
        "Presence activity is keyed by the caller's own user_id; the DELETE "
        "predicate and cache invalidation are bound to that one id.",
    ),
    BulkOp(
        "clear custom status",
        "src/core/presence/manager/custom_status.py",
        "clear_custom_status",
        "self_scoped",
        ("DELETE FROM pres_custom_status WHERE user_id = ?",),
        "Custom status rows are deleted only for the supplied user id; the "
        "same validation applies to the single-user set path.",
    ),
    BulkOp(
        "clear all typing",
        "src/core/presence/manager/typing.py",
        "clear_all_typing",
        "self_scoped",
        ("WHERE user_id = ?",),
        "Disconnect cleanup drops typing indicators only for the disconnecting "
        "connection's own user_id, never for other users.",
    ),
    BulkOp(
        "clear storage override",
        "src/core/storage_quota/manager.py",
        "clear_override",
        "self_scoped",
        ("DELETE FROM user_storage_quotas WHERE user_id = ?",),
        "Override deletion is bound to the single user_id argument and its "
        "cache key; include_override never changes the row scope.",
    ),
    BulkOp(
        "bulk conversation access resolution",
        "src/core/search/manager/access.py",
        "_can_access_conversations_bulk",
        "self_scoped",
        ("WHERE user_id = ?",),
        "Read-only batched DM access; the participant lookup is bound to the "
        "requesting user_id so no conversation of another user is exposed.",
    ),
    BulkOp(
        "load existing settings bulk",
        "src/core/settings/manager.py",
        "_get_existing_bulk",
        "self_scoped",
        ("WHERE user_id = ?",),
        "Read helper bounded to one user's settings rows; used by the bulk "
        "write path to avoid per-key probes without widening ownership.",
    ),
    BulkOp(
        "get all settings",
        "src/core/settings/manager.py",
        "get_all",
        "self_scoped",
        ("WHERE user_id = ?",),
        "Read-only; every settings row returned is restricted to the supplied "
        "user id, so the batch cannot disclose another user's settings.",
    ),
    BulkOp(
        "grant workbook permissions bulk",
        "src/core/suite/sum/manager_base.py",
        "_grant_permissions_bulk",
        "self_scoped",
        ("subject_id = ?",),
        "Resolves live grants for one viewer across many workbooks; the query "
        "is bound to the single subject id, so no other viewer's grants leak.",
    ),
    BulkOp(
        "purge temporary members",
        "src/core/servers/manager/base.py",
        "purge_temporary_members",
        "self_scoped",
        ("self.member_handler.purge_temporary_members",),
        "Self-scoped WS path: the disconnect gateway passes only its own "
        "connection user_id, and the downstream handler deletes temporary "
        "memberships with WHERE user_id = ? AND is_temporary = 1.",
    ),
    # -- callers are gated ---------------------------------------------------
    BulkOp(
        "set feature settings bulk",
        "src/core/artifacts/manager.py",
        "set_feature_settings_bulk",
        "route_gated",
        ("set_feature_settings_bulk", "db and server_id are required"),
        "Thin repository delegation; its only route caller is admin/artifacts.py "
        "behind require_admin_permission('admin.artifacts.manage').",
    ),
    BulkOp(
        "reindex all messages",
        "src/core/search/manager/message_indexing.py",
        "reindex_all",
        "route_gated",
        ("ReindexCancelled",),
        "Destructive full rebuild; its only callers are admin/reindex.py which "
        "requires admin.data.reindex, maintenance scope and an approval gate.",
    ),
    # -- documented allowlist ------------------------------------------------
    BulkOp(
        "device bulk read",
        "src/core/auth/managers/device.py",
        "get_devices_bulk",
        "allowlist",
        ("get_devices_bulk",),
        "Read-only batched lookup with explicit requested-id filtering; no "
        "persisted state is changed and no cross-user row can be returned.",
    ),
    BulkOp(
        "profile bulk read",
        "src/core/auth/managers/profile.py",
        "get_user_profiles_bulk",
        "allowlist",
        ("get_user_profiles_bulk",),
        "Read-only profile snapshots over a caller-supplied id set; no writes "
        "and the caller already held each id from an authorized list.",
    ),
    BulkOp(
        "users bulk read",
        "src/core/auth/managers/profile.py",
        "get_users_bulk",
        "allowlist",
        ("get_users_bulk",),
        "Read-only bounded IN lookup; the requested id set is de-duplicated "
        "and no state is mutated.",
    ),
    BulkOp(
        "logout all protocol stub",
        "src/core/auth/managers/protocol.py",
        "logout_all",
        "allowlist",
        ("...",),
        "Abstract protocol declaration (ellipsis body) with no side effects; "
        "the concrete implementation is the self_scoped lifecycle entry.",
    ),
    BulkOp(
        "sessions bulk read",
        "src/core/auth/managers/session/lifecycle.py",
        "get_sessions_bulk",
        "allowlist",
        ("get_sessions_bulk",),
        "Read-only batched session lookup; revoked rows are filtered and rows "
        "are grouped by their own user id, so no state is mutated.",
    ),
    BulkOp(
        "clear 2FA attempt buckets",
        "src/core/auth/managers/twofactor.py",
        "clear_twofa_attempts",
        "allowlist",
        ("_TWOFA_ATTEMPTS.clear",),
        "Drops in-process attempt buckets only (tests/config reload); no "
        "persisted auth state or cross-user record is touched.",
    ),
    BulkOp(
        "bulk automod scan",
        "src/core/automod/manager/evaluation.py",
        "scan_messages_bulk",
        "allowlist",
        ("scan_messages_bulk",),
        "Read-only evaluation of already-fetched messages for automod rules; "
        "it returns findings and performs no bulk state change itself.",
    ),
    BulkOp(
        "clear envelope template cache",
        "src/core/events/manager.py",
        "clear_envelope_template_cache",
        "allowlist",
        ("_envelope_templates.clear",),
        "In-process template memo invalidation only; no durable record is "
        "deleted, so there is no cross-user authorization surface.",
    ),
    BulkOp(
        "message bulk-delete facade",
        "src/core/messaging/manager.py",
        "delete_messages_bulk",
        "allowlist",
        ("self._message_svc.delete_messages_bulk",),
        "Thin delegator to the gated edit_delete.py bulk-delete entry; it adds "
        "no capability and passes the caller id through unchanged.",
    ),
    BulkOp(
        "mark read bulk facade",
        "src/core/messaging/manager.py",
        "mark_read_bulk",
        "allowlist",
        ("self._message_status_svc.mark_read_bulk",),
        "Thin delegator to the scoped message_status.py entry; the route also "
        "enforces the per-channel view gate before calling it.",
    ),
    BulkOp(
        "bulk delete protocol stub",
        "src/core/messaging/services/message/protocol.py",
        "delete_messages_bulk",
        "allowlist",
        ("...",),
        "Abstract protocol declaration with no body; the concrete gated "
        "implementation is the edit_delete.py entry.",
    ),
    BulkOp(
        "bulk archive protocol stub",
        "src/core/messaging/services/message/protocol.py",
        "archive_messages_bulk",
        "allowlist",
        ("...",),
        "Abstract protocol declaration with no body; the concrete gated "
        "implementation is the edit_delete.py entry.",
    ),
    BulkOp(
        "mark read bulk service",
        "src/core/messaging/services/message_status.py",
        "mark_read_bulk",
        "allowlist",
        ("batch_mark_read_bulk",),
        "Delegates to a repository method whose UPDATE/INSERT predicates are "
        "bound to the supplied user_id and channel set; the route gates view "
        "access per channel first.",
    ),
    BulkOp(
        "unread cache get all",
        "src/core/messaging/services/unread_cache.py",
        "get_all",
        "allowlist",
        ("mem_cache_get",),
        "Read-only in-process cache accessor keyed by user id; returns a "
        "defensive copy and changes no state.",
    ),
    BulkOp(
        "unread cache put all",
        "src/core/messaging/services/unread_cache.py",
        "put_all",
        "allowlist",
        ("mem_cache_set",),
        "Writes only an in-process cache entry keyed by user id; no durable "
        "record is changed and the key cannot address another user's row.",
    ),
    BulkOp(
        "unread cache invalidate all",
        "src/core/messaging/services/unread_cache.py",
        "invalidate_all",
        "allowlist",
        ("mem_cache_clear_pattern",),
        "Pattern-clears only in-process unread memos after a message write; "
        "no persisted data is deleted and a miss simply recomputes.",
    ),
    BulkOp(
        "role members bulk read",
        "src/core/notifications/manager/creator.py",
        "get_role_members_bulk",
        "allowlist",
        ("get_role_members_bulk",),
        "Read-only bounded IN lookup used to fan out notifications; it does "
        "not change any state and takes no route-supplied target ids.",
    ),
    BulkOp(
        "bump unread counts bulk",
        "src/core/notifications/manager/creator.py",
        "_bump_unread_counts_bulk",
        "allowlist",
        ("user_ids", "conversation_id"),
        "Internal fan-out helper: writes only the explicitly supplied "
        "recipient ids and conversation id after the message send was already "
        "authorized; it accepts no independent route input.",
    ),
    BulkOp(
        "create notifications bulk",
        "src/core/notifications/manager/creator.py",
        "create_notifications_bulk",
        "allowlist",
        ("already_notified",),
        "Internal notification fan-out for an already-authorized sent message; "
        "UNIQUE(user_id, message_id) makes replays no-ops and it exposes no "
        "route-callable surface of its own.",
    ),
    BulkOp(
        "notification settings bulk protocol",
        "src/core/notifications/manager/protocol.py",
        "get_notification_settings_bulk",
        "allowlist",
        ("super()",),
        "Read-only protocol forwarder to the settings service; no state is "
        "changed and the settings themselves are self-keyed by user id.",
    ),
    BulkOp(
        "channel overrides bulk protocol",
        "src/core/notifications/manager/protocol.py",
        "get_channel_overrides_bulk",
        "allowlist",
        ("super()",),
        "Read-only protocol forwarder to the settings service; no state is "
        "changed and the override rows are self-keyed by user id.",
    ),
    BulkOp(
        "notification settings bulk read",
        "src/core/notifications/manager/settings.py",
        "get_notification_settings_bulk",
        "allowlist",
        ("get_notification_settings_bulk",),
        "Read-only batched settings lookup; rows are grouped by their own "
        "user id and no write is performed.",
    ),
    BulkOp(
        "channel overrides bulk read",
        "src/core/notifications/manager/settings.py",
        "get_channel_overrides_bulk",
        "allowlist",
        ("get_channel_overrides_bulk",),
        "Read-only batched override lookup keyed by user/channel; no persisted "
        "state is mutated.",
    ),
    BulkOp(
        "clear presence status",
        "src/core/presence/manager/status.py",
        "clear_status",
        "allowlist",
        ("UserStatus.OFFLINE",),
        "Delegates to set_status(user_id, OFFLINE), which is self-scoped to the "
        "supplied user; it introduces no bulk capability of its own.",
    ),
    BulkOp(
        "visible presences bulk read",
        "src/core/presence/manager/visibility.py",
        "get_visible_presences_bulk",
        "allowlist",
        ("get_visible_presences_bulk",),
        "Read-only presence snapshot filtered by the viewer's visibility "
        "rules; no durable state is changed.",
    ),
    BulkOp(
        "profile clear custom status",
        "src/core/profiles/manager.py",
        "clear_custom_status",
        "allowlist",
        ("set_custom_status",),
        "Delegates to set_custom_status(user_id, ...) which is scoped to that "
        "one user; the batch name is a naming artifact, not a cross-user op.",
    ),
    BulkOp(
        "reset all rate buckets",
        "src/core/ratelimit/manager/core.py",
        "reset_all",
        "allowlist",
        ("clear_all",),
        "Resets in-memory limiter buckets for maintenance/tests only; no "
        "durable authorization record is deleted.",
    ),
    BulkOp(
        "async reset all rate buckets",
        "src/core/ratelimit/manager/core.py",
        "areset_all",
        "allowlist",
        ("aclear_all",),
        "Awaitable twin of reset_all with the same maintenance-only, "
        "in-memory-only scope and no persisted side effect.",
    ),
    BulkOp(
        "clear route canonicalize cache",
        "src/core/ratelimit/manager/route_keys.py",
        "clear_canonicalize_cache",
        "allowlist",
        ("cache_clear",),
        "Clears a module-level lru_cache of canonicalized route strings; "
        "tests/config-reload only, no durable state involved.",
    ),
    BulkOp(
        "reactions bulk read",
        "src/core/reactions/manager/reactions.py",
        "get_reactions_bulk",
        "allowlist",
        ("get_reactions_batch",),
        "Read-only batched reaction lookup with credential-based access "
        "filtering; it deletes nothing and returns only viewable rows.",
    ),
    BulkOp(
        "blocked ids bulk read",
        "src/core/relationships/manager/blocking.py",
        "get_all_blocked_ids_bulk",
        "allowlist",
        ("get_all_blocked_ids_bulk",),
        "Read-only two-way block-set resolution over the requested ids; no "
        "state is mutated and the single-user twin is equally read-only.",
    ),
    BulkOp(
        "friend ids bulk read",
        "src/core/relationships/manager/friends.py",
        "get_friend_ids_bulk",
        "allowlist",
        ("get_friend_ids_bulk",),
        "Read-only batched friend lookup; rows are grouped by their own user "
        "id and no write is performed.",
    ),
    BulkOp(
        "mutual servers bulk read",
        "src/core/relationships/manager/mutual.py",
        "get_mutual_servers_bulk",
        "allowlist",
        ("get_mutual_servers_bulk",),
        "Read-only shared-server resolution for one user against target ids; "
        "the participant predicate is bound to the requesting user id.",
    ),
    BulkOp(
        "friend ids bulk protocol",
        "src/core/relationships/manager/protocol.py",
        "get_friend_ids_bulk",
        "allowlist",
        ("...",),
        "Abstract protocol declaration with no side effects; the concrete "
        "read-only implementation is the friends.py entry.",
    ),
    BulkOp(
        "blocked ids bulk protocol",
        "src/core/relationships/manager/protocol.py",
        "get_all_blocked_ids_bulk",
        "allowlist",
        ("...",),
        "Abstract protocol declaration with no side effects; the concrete "
        "read-only implementation is the blocking.py entry.",
    ),
    BulkOp(
        "mutual servers bulk protocol",
        "src/core/relationships/manager/protocol.py",
        "get_mutual_servers_bulk",
        "allowlist",
        ("...",),
        "Abstract protocol declaration with no side effects; the concrete "
        "read-only implementation is the mutual.py entry.",
    ),
    BulkOp(
        "listings bulk read",
        "src/core/search/discovery/manager.py",
        "get_listings_bulk",
        "allowlist",
        ("get_listings_bulk",),
        "Read-only discovery listing lookup over public server ids; no state "
        "is mutated and the rows are grouped by their own server id.",
    ),
    BulkOp(
        "search base cache clear",
        "src/core/search/manager/base.py",
        "clear_cache",
        "allowlist",
        ("_search_rate_count.clear",),
        "Clears only in-process rate-window counters; no durable record is "
        "deleted and a cold counter merely recomputes.",
    ),
    BulkOp(
        "message search cache clear",
        "src/core/search/manager/message_search.py",
        "clear_cache",
        "allowlist",
        ("clear_result_caches",),
        "Invalidates short-TTL in-process search memos after index/permission "
        "changes; no persisted data is removed.",
    ),
    BulkOp(
        "result caches clear",
        "src/core/search/manager/result_cache.py",
        "clear_result_caches",
        "allowlist",
        ("cache.clear",),
        "Empties in-process search-result memos only; invalidation is "
        "idempotent and recomputes from the authoritative index.",
    ),
    BulkOp(
        "server search cache clear",
        "src/core/search/manager/server_search.py",
        "clear_cache",
        "allowlist",
        ("clear_result_caches",),
        "Delegates to the in-process result-cache clear plus the base rate "
        "counter clear; no durable state is touched.",
    ),
    BulkOp(
        "user search cache clear",
        "src/core/search/manager/user_search.py",
        "clear_cache",
        "allowlist",
        ("clear_result_caches",),
        "Delegates to the in-process result-cache clear; no durable state is "
        "touched and memos are keyed by the authorized viewer.",
    ),
    BulkOp(
        "delete threads for channel",
        "src/core/threads/manager/statemgmt.py",
        "delete_threads_for_channel",
        "allowlist",
        ("thread_threads WHERE channel_id = ?",),
        "Only caller is the channel-deletion handler, which first enforces "
        "the server-scoped channels.manage permission; the manager is never "
        "route-callable with an arbitrary channel id.",
    ),
    BulkOp(
        "clear voice lookup cache",
        "src/core/voice/manager/base.py",
        "clear_voice_lookup_cache",
        "allowlist",
        ("_invalidate_all_lookups",),
        "Invalidates in-process voice lookups for setup/diagnostics; no "
        "persisted authorization data is deleted.",
    ),
    BulkOp(
        "channel user counts bulk read",
        "src/core/voice/manager/base.py",
        "_get_channel_user_counts_bulk",
        "allowlist",
        ("_get_channel_user_counts_bulk",),
        "Read-only GROUP BY lookup for list endpoints; bounded IN chunks and "
        "no state mutation.",
    ),
    BulkOp(
        "channel settings bulk read",
        "src/core/voice/manager/base.py",
        "_get_channel_settings_bulk",
        "allowlist",
        ("_get_channel_settings_bulk",),
        "Read-only batched settings lookup; no write is performed and callers "
        "already passed the channel view gate.",
    ),
    BulkOp(
        "resolve conversation for channel",
        "src/core/voice/manager/calls.py",
        "_resolve_conversation_for_channel",
        "allowlist",
        ("_resolve_conversation_for_channel",),
        "Best-effort read-only lookup of a channel's backing conversation; "
        "no state change regardless of the channel id supplied.",
    ),
    BulkOp(
        "grant workbook permissions protocol stub",
        "src/core/suite/sum/manager_protocol.py",
        "_grant_permissions_bulk",
        "allowlist",
        ("NotImplementedError",),
        "Abstract protocol declaration that raises NotImplementedError, so it "
        "has no side effects; the concrete scoped implementation is in "
        "suite/sum/manager_base.py.",
    ),
    BulkOp(
        "channel user counts bulk protocol",
        "src/core/voice/manager/protocol.py",
        "_get_channel_user_counts_bulk",
        "allowlist",
        ("return {}",),
        "No-op protocol fallback with no side effects; the concrete "
        "read-only implementation is in voice/manager/base.py.",
    ),
    BulkOp(
        "channel settings bulk protocol",
        "src/core/voice/manager/protocol.py",
        "_get_channel_settings_bulk",
        "allowlist",
        ("return {}",),
        "No-op protocol fallback with no side effects; the concrete "
        "read-only implementation is in voice/manager/base.py.",
    ),
)

REGISTRY: dict[tuple[str, str], BulkOp] = {
    (op.rel_path, op.func): op for op in BULK_OPS
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _tree(commit: str, path: str, reader) -> ast.Module:
    return ast.parse(reader(path))


def _find_function(tree: ast.AST, func: str) -> ast.AST:
    matches = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func
    ]
    assert len(matches) == 1, f"expected exactly one {func!r}, found {len(matches)}"
    return matches[0]


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return ""


@dataclass(frozen=True)
class Caller:
    path: str
    func: str
    source: str


def _own_calls(node: ast.AST):
    """Calls in a function's own body, excluding nested function/class bodies."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(child, ast.Call):
            yield child
        yield from _own_calls(child)


def _callers_in_source(source: str, *, path: str, func: str) -> list[Caller]:
    """Route/WS handlers in ``source`` that dispatch ``func``.

    A dispatch often lives in a nested worker (thread/background task) while
    the authorization gate sits in the enclosing route handler. Such a caller
    is gated, so the source recorded for a call includes its whole ancestor
    function chain -- not just the innermost function.
    """
    tree = ast.parse(source)
    found: list[Caller] = []

    def visit(node: ast.AST, chain: tuple[ast.AST, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, chain + (child,))
            else:
                visit(child, chain)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            _call_name(call) == func for call in _own_calls(node)
        ):
            body = " ".join(ast.unparse(f) for f in chain + (node,))
            found.append(Caller(path, node.name, body))

    visit(tree, ())
    return found


def _ungated_callers(callers: list[Caller]) -> list[Caller]:
    """Callers with no authorization/self-scope marker anywhere in the body."""
    return [
        c
        for c in callers
        if not any(marker in c.source for marker in CALLER_GATE_MARKERS)
    ]


@lru_cache(maxsize=None)
def _route_files(commit: str) -> tuple[str, ...]:
    return _tracked_files(commit, "src/api")


def _route_callers(commit: str, func: str, reader) -> list[Caller]:
    found: list[Caller] = []
    for path in _route_files(commit):
        source = reader(path)
        if func not in source:
            continue
        found.extend(_callers_in_source(source, path=path, func=func))
    return found


def _identity_keyed_delegation(fn: ast.AST) -> bool:
    """Signature has a caller identity and the body delegates to a writer."""
    args = [a.arg for a in fn.args.args]  # type: ignore[attr-defined]
    identity = {"user_id", "viewer_id", "current_user_id", "principal_id"}
    if not (identity & set(args)):
        return False
    body = ast.unparse(fn)
    return "self." in body or "_svc." in body or "module." in body


# ---------------------------------------------------------------------------
# Assessment: registry entries and brand-new operations share one code path
# ---------------------------------------------------------------------------


def _assess(
    op: BulkOp | None,
    fn_source: str,
    callers: list[Caller],
    *,
    fn: ast.AST | None = None,
) -> list[str]:
    """Return a list of safety problems; empty means the op is acceptable."""
    problems: list[str] = []
    if op is None:
        # Brand-new, undocumented op: fail closed unless it carries its own
        # authoritative gate, or every route caller is gated/self-scoped.
        if any(m in fn_source for m in GATE_MARKERS):
            return problems
        ungated = _ungated_callers(callers)
        if ungated:
            problems.append(
                "NEW bulk destructive op has no internal gate and an ungated "
                "route caller: " + ", ".join(f"{c.path}:{c.func}()" for c in ungated)
            )
        return problems

    for marker in op.evidence:
        if marker not in fn_source:
            problems.append(
                f"{op.rel_path}:{op.func}() lost evidence marker {marker!r}"
            )

    has_gate = any(m in fn_source for m in GATE_MARKERS)
    has_scope = any(m in fn_source for m in SCOPE_MARKERS)
    if op.verdict == "gate" and not has_gate:
        problems.append(f"{op.func}(): verdict 'gate' but no gate marker remains")
    elif op.verdict == "self_scoped" and not (
        has_scope or (fn is not None and _identity_keyed_delegation(fn))
    ):
        problems.append(
            f"{op.func}(): verdict 'self_scoped' but no scope predicate remains"
        )
    elif op.verdict == "route_gated":
        ungated = _ungated_callers(callers)
        if ungated:
            problems.append(
                f"{op.func}(): ungated route caller(s): "
                + ", ".join(f"{c.path}:{c.func}()" for c in ungated)
            )
    elif op.verdict == "allowlist":
        if len(op.why.strip()) < 40:
            problems.append(f"{op.func}(): allowlist entry needs a real reason")
    elif op.verdict not in {"gate", "self_scoped", "route_gated", "allowlist"}:
        problems.append(f"{op.func}(): unknown verdict {op.verdict!r}")
    return problems


def _discover(commit: str, reader) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in _tracked_files(commit, "src/core"):
        if not _in_scope(path):
            continue
        tree = _tree(commit, path, reader)
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and _is_bulk_name(node.name):
                found.add((path, node.name))
    return found


# ---------------------------------------------------------------------------
# Non-vacuity self-tests (pure in-memory strings)
# ---------------------------------------------------------------------------


def test_self_test_ungated_purge_route_caller_is_flagged():
    source = textwrap.dedent(
        """
        @router.post("/purge")
        async def purge_everything(request: Request):
            return manager.purge_all_X(request)
        """
    )
    callers = _callers_in_source(
        source, path="src/api/routes/_synthetic.py", func="purge_all_X"
    )
    assert len(callers) == 1
    assert _ungated_callers(callers) == callers  # synthetic op is flagged
    assert _assess(None, "def purge_all_X(self, request): ...", callers), (
        "a new ungated purge op with an ungated route caller must fail closed"
    )


def test_self_test_gated_and_self_scoped_callers_are_accepted():
    gated = textwrap.dedent(
        """
        @router.post("/purge")
        async def purge_everything(request: Request):
            require_admin_permission(request, "admin.purge")
            return manager.purge_all_X(request)
        """
    )
    scoped = textwrap.dedent(
        """
        @router.post("/purge")
        async def purge_mine(request: Request):
            return manager.purge_all_X(current_user.user_id)
        """
    )
    for source in (gated, scoped):
        callers = _callers_in_source(
            source, path="src/api/routes/_synthetic.py", func="purge_all_X"
        )
        assert len(callers) == 1
        assert _ungated_callers(callers) == []
        assert _assess(None, "def purge_all_X(self, request): ...", callers) == [], (
            "gated/self-scoped callers must not be flagged"
        )


def test_self_test_internal_gate_suppresses_route_concern():
    # A brand-new op with its own authoritative gate is acceptable even if a
    # route caller forgets to repeat the check.
    callers = _callers_in_source(
        textwrap.dedent(
            """
            @router.post("/purge")
            async def purge_everything(request: Request):
                return manager.purge_all_X(request)
            """
        ),
        path="src/api/routes/_synthetic.py",
        func="purge_all_X",
    )
    gated_body = textwrap.dedent(
        """
        def purge_all_X(self, user_id):
            if not self._check_server_permission(user_id, server_id):
                raise PermissionDeniedError("denied")
            self._db.execute("DELETE FROM X WHERE user_id = ?", (user_id,))
        """
    )
    assert _assess(None, gated_body, callers) == []


def test_self_test_per_entry_gate_check_fails_when_marker_removed():
    entry = BulkOp(
        "synthetic",
        "src/core/_synthetic/manager.py",
        "purge_all_X",
        "gate",
        ("PermissionDeniedError",),
        "Synthetic gate entry that must fail when its marker disappears.",
    )
    good = textwrap.dedent(
        """
        def purge_all_X(self, user_id):
            if not allowed:
                raise PermissionDeniedError("denied")
        """
    )
    bad = textwrap.dedent(
        """
        def purge_all_X(self, user_id):
            self._db.execute("DELETE FROM X")
        """
    )
    assert _assess(entry, good, []) == []
    problems = _assess(entry, bad, [])
    assert any("lost evidence" in p or "no gate marker" in p for p in problems)


def test_self_test_allowlist_case_accepted_but_requires_reason():
    good = BulkOp(
        "synthetic",
        "src/core/_synthetic/manager.py",
        "get_all_X",
        "allowlist",
        ("get_all_X",),
        "Read-only batched lookup over an explicit id set; no durable state is "
        "mutated at all, so there is no authorization surface here.",
    )
    assert _assess(good, "def get_all_X(self, ids): return ids", []) == []
    thin = BulkOp(
        "synthetic",
        "src/core/_synthetic/manager.py",
        "get_all_X",
        "allowlist",
        ("get_all_X",),
        "read only",
    )
    assert _assess(thin, "def get_all_X(self, ids): return ids", [])


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_registry_is_well_formed():
    assert len(BULK_OPS) >= 60, "registry lost documented operations"
    assert len(REGISTRY) == len(BULK_OPS), "duplicate (path, func) registry entries"
    for op in BULK_OPS:
        assert op.rel_path.startswith("src/core/"), op.rel_path
        assert _in_scope(op.rel_path), f"{op.rel_path} is outside the manager scope"
        assert _is_bulk_name(op.func), f"{op.func} does not match a bulk pattern"
        assert op.evidence, f"{op.label}: no evidence markers"
        assert op.why.strip(), f"{op.label}: empty allowlist reason"


def test_scan_is_not_vacuous(commit_sha):
    files = [p for p in _tracked_files(commit_sha, "src/core") if _in_scope(p)]
    assert len(files) >= 100, f"only {len(files)} in-scope modules scanned"
    reader = _make_blob_reader(commit_sha)
    # The reader really returns committed content: these contested files must
    # still carry their gates in HEAD regardless of parallel working copies.
    assert "WHERE user_id = ?" in reader("src/core/auth/managers/session/lifecycle.py")
    assert "_check_server_permission" in reader(
        "src/core/reactions/manager/reactions.py"
    )
    assert len(_route_files(commit_sha)) >= 100, "route caller scan is vacuous"


def test_discovery_matches_registry_exactly(commit_sha):
    reader = _make_blob_reader(commit_sha)
    discovered = _discover(commit_sha, reader)
    expected = set(REGISTRY)
    assert discovered == expected, (
        "bulk-op registry drifted from the committed tree.\n"
        f"  NEW undocumented ops (fail closed): {sorted(discovered - expected)}\n"
        f"  stale registry entries: {sorted(expected - discovered)}"
    )


def test_every_documented_op_keeps_its_gate_or_scope(commit_sha):
    reader = _make_blob_reader(commit_sha)
    assert len(REGISTRY) >= 60
    for op in BULK_OPS:
        tree = _tree(commit_sha, op.rel_path, reader)
        fn = _find_function(tree, op.func)
        callers = (
            _route_callers(commit_sha, op.func, reader)
            if op.verdict == "route_gated"
            else []
        )
        problems = _assess(op, ast.unparse(fn), callers, fn=fn)
        assert not problems, f"{op.rel_path}:{op.func}(): " + "; ".join(problems)


def test_committed_tree_sweep_detector_is_non_vacuous(commit_sha):
    # The discovery sweep must find the documented operations -- if it found
    # none, "discovered == registry" would be satisfied by an empty registry.
    reader = _make_blob_reader(commit_sha)
    discovered = _discover(commit_sha, reader)
    for path, func in (
        ("src/core/threads/manager/statemgmt.py", "delete_threads_for_channel"),
        ("src/core/servers/manager/base.py", "purge_temporary_members"),
        ("src/core/auth/managers/session/lifecycle.py", "revoke_sessions_bulk"),
        ("src/core/reactions/manager/reactions.py", "remove_all_reactions"),
        ("src/core/messaging/services/message/edit_delete.py", "delete_messages_bulk"),
    ):
        assert (path, func) in discovered, f"sweep missed {path}:{func}()"


def test_no_new_ungated_bulk_destructive_op_in_committed_tree(commit_sha):
    """Every discovered op passes _assess; new ones fail closed on gate/route."""
    reader = _make_blob_reader(commit_sha)
    discovered = _discover(commit_sha, reader)
    problems: list[str] = []
    for path, func in sorted(discovered):
        tree = _tree(commit_sha, path, reader)
        fn = _find_function(tree, func)
        callers = _route_callers(commit_sha, func, reader)
        op = REGISTRY.get((path, func))
        for problem in _assess(op, ast.unparse(fn), callers, fn=fn):
            problems.append(f"{path}:{func}(): {problem}")
    assert not problems, "\n".join(problems)
