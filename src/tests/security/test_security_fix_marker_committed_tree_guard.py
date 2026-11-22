"""Guard: critical security fixes must remain in the COMMITTED tree.

Background
----------
Several contested files were repaired through private-index commits (N448
``d39659c3``, N478 ``d6a4143``, N459 ``c6a111dd``).  The fix therefore exists
at ``HEAD`` while peer agents' working-tree/index copies of those same files
are *stale* -- they still contain the pre-fix code (``max(0, limit)``, a default
cache key, a curated file list instead of the structural sweep, ...).

If a peer later stages one of those whole stale files, the resulting commit can
silently REVERT the fix.  The existing regression tests would notice that at
test time, but a static/committed-tree guard catches it earlier and names the
exact fix that disappeared.

Design
------
This module is the *committed-tree* counterpart of the working-tree
``test_security_fix_marker_meta_guard.py``.  A single documented registry
(:data:`COMMITTED_FIX_MARKERS`) lists only the highest-risk markers from those
private-index repairs.  For each entry we read the blob straight out of the
commit via ``git show <sha>:<path>`` -- never the working tree -- so a stale
working copy cannot mask a committed revert (and cannot produce a false alarm
either).

HEAD is resolved once per test session and pinned for every blob read, which
keeps the check internally consistent even while other agents keep moving HEAD
concurrently; every run still reads the *current* HEAD at runtime.

Removal policy: a path that disappears from the commit is a failure, not a
silent pass.  Removal is tolerated only for an explicitly documented successor
(``successors``), mirroring the working-tree meta-guard's ``moved_to``.

Non-vacuity is proven twice: (1) a fake blob with the marker stripped out is
flagged for every registry entry, and (2) a throwaway git repo whose working
tree diverges from HEAD shows the reader really returns the committed blob.

Negative (``absent``) markers
-----------------------------
Some fixes are the *absence* of a vulnerable pattern (the query-cache
classifier must not treat ``#`` as a comment, and must not list ``EXPLAIN`` as
a read prefix).  Such entries set ``absent=True`` and pass only while the
pattern is missing from the committed blob; an optional ``scope`` limits the
search to one lexical block so a pattern named in an adjacent rationale
comment is not a false positive.  Non-vacuity is proven from the inverted side:
a synthetic committed blob that REINTRODUCES the pattern must be flagged.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


class BlobMissing(Exception):
    """Raised by a blob reader when a path is absent from the commit."""

    def __init__(self, path: str, reason: str = "") -> None:
        super().__init__(f"{path!r} not present in commit: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class CommittedFixMarker:
    """One critical fix that must be present in a committed blob.

    ``path`` is repo-relative.  ``successors`` are documented relocation
    targets: a primary-path removal is tolerated only when a successor blob
    carries the marker.  ``why`` records the security purpose and the stale-file
    revert that this entry is meant to catch.

    ``absent=True`` inverts the check: the entry passes while ``marker`` is
    absent and FAILS if the vulnerable pattern is committed.  ``scope`` optionally
    bounds the search to the text between two literal anchors.
    """

    key: str
    path: str
    marker: str
    why: str
    successors: tuple[str, ...] = field(default_factory=tuple)
    absent: bool = False
    scope: tuple[str, str] | None = None

    @property
    def candidates(self) -> tuple[str, ...]:
        return (self.path,) + self.successors


# The two _verify_ws_ticket copies (TicketMixin's active implementation and the
# LifecycleMixin mirror) share the same binding block. Pin the constant-time
# compares independently so a stale staged copy of either file that drops the
# ip_index or ua_index check is caught by name. The distinctive
# `srow.get("ip_index"/"ua_index")` anchors keep each pin local to the ticket
# verifier rather than any other session-binding compare in the same file.
_WS_TICKET_IP_BINDING_COMPARE = (
    '            stored_ip_index = srow.get("ip_index")\n'
    "            if not isinstance(stored_ip_index, str) or not stored_ip_index:\n"
    '                raise TokenInvalidError("IP Binding Required")\n'
    "            if not hmac.compare_digest(\n"
    "                stored_ip_index,\n"
    '                self.crypto.fast_blind_index(ip_address, "ip_address"),\n'
    "            ):\n"
    '                raise TokenInvalidError("IP Binding Mismatch")'
)

_WS_TICKET_UA_BINDING_COMPARE = (
    '            stored_ua_index = srow.get("ua_index")\n'
    "            if not isinstance(stored_ua_index, str) or not stored_ua_index:\n"
    '                raise TokenInvalidError("User-Agent Binding Required")\n'
    "            current_ua_index = self._ua_index(user_agent)\n"
    "            if not isinstance(current_ua_index, str) or not current_ua_index:\n"
    '                raise TokenInvalidError("User-Agent Binding Required")\n'
    "            if not hmac.compare_digest(stored_ua_index, current_ua_index):\n"
    '                raise TokenInvalidError("User-Agent Binding Mismatch")'
)

# cdeda33b (N700 rank re-check + N642 grantability re-check): assign_role
# re-reads the authoritative role row under the 142 -> 143 advisory locks
# and re-runs BOTH the grantability and rank/hierarchy decisions before the
# insert. Each block pins the soft-delete predicate, the explicit fail-closed
# raise on a missing row, and the re-decision call itself, so a stale copy
# that drops either re-check under the lock is caught by name.
_ROLE_ASSIGNMENT_GRANTABILITY_RECHECK = (
    '            fresh_row = self.db.fetch_one(\n'
    '                "SELECT permissions FROM srv_roles WHERE id = ? AND deleted = 0",\n'
    '                (role_id,),\n'
    '            )\n'
    '            if not fresh_row:\n'
    '                raise RoleNotFoundError("Role not found")\n'
    '            fresh_permissions = fresh_row["permissions"]\n'
    '            if isinstance(fresh_permissions, str):\n'
    '                try:\n'
    '                    fresh_permissions = json.loads(fresh_permissions)\n'
    '                except (TypeError, ValueError):\n'
    '                    raise InvalidPermissionKeyError(\n'
    '                        "Role permissions are unreadable"\n'
    '                    )\n'
    '            if not isinstance(fresh_permissions, dict):\n'
    '                raise InvalidPermissionKeyError("Role permissions are unreadable")\n'
    '            validate_permission_dict(fresh_permissions)\n'
    '            check_permissions_grantable(\n'
    '                self.get_permissions(user_id, server_id), fresh_permissions\n'
    '            )'
)

_ROLE_ASSIGNMENT_RANK_RECHECK = (
    '            fresh_position = self.db.fetch_one(\n'
    '                "SELECT position FROM srv_roles WHERE id = ? AND deleted = 0",\n'
    '                (role_id,),\n'
    '            )\n'
    '            if not fresh_position:\n'
    '                raise RoleNotFoundError("Role not found")\n'
    '            current_user_roles = self.manager._get_member_role_rows(\n'
    '                server_id, user_id\n'
    '            )\n'
    '            if not can_manage_role(\n'
    '                current_user_roles,\n'
    '                {"position": fresh_position["position"]},\n'
    '                is_owner,\n'
    '            ):\n'
    '                raise RoleHierarchyError(\n'
    '                    "Cannot assign a role at or above your highest role"\n'
    '                )\n'
    '            self._guard_member_target(user_id, server_id, member_user_id)'
)

# ded43f8e (N644): refresh_session re-checks the PRESENTED refresh credential's
# IP/UA against the session's stored blind indexes in constant time before the
# atomic consume. These two single-line compare_digest calls (with the
# `return None` fail-closed arms) are distinctive to refresh_session -- the
# WS-ticket verifier compares use the multi-line form with TokenInvalidError --
# so a stale staged copy of lifecycle.py that drops the binding is caught by name.
_REFRESH_SESSION_CONTEXT_BINDING = (
    "            if not hmac.compare_digest(stored_ip_index, current_ip_index):\n"
    "                return None\n"
    "            if not user_agent:\n"
    "                return None\n"
    '            stored_ua_index = row.get("ua_index")\n'
    "            if not isinstance(stored_ua_index, str) or not stored_ua_index:\n"
    "                return None\n"
    "            current_ua_index = self._ua_index(user_agent)\n"
    "            if not hmac.compare_digest(stored_ua_index, current_ua_index):\n"
    "                return None"
)


# --- OAuth invariant anchors (e85eab23 / N654 / N689) ---
# The state cookie clear must mirror issuance in every protective attribute,
# and the redirect_uri allow-list must stay a literal exact-string match.
# These multi-line anchors are distinctive to the repaired blobs, so a stale
# staged copy that restores a bare delete_cookie or a normalising/prefix
# comparison is caught by name.
_OAUTH_STATE_COOKIE_ISSUANCE_BLOCK = (
    "    return {\n"
    '        "max_age": max_age,\n'
    '        "httponly": True,\n'
    '        "secure": session_cookie_secure(),\n'
    '        "samesite": "lax",\n'
    "        # Covers both /auth/oauth/{provider}/login and .../callback.\n"
    '        "path": f"{api_prefix}/auth/oauth",\n'
    "    }"
)

_OAUTH_STATE_COOKIE_CLEAR_BLOCK = (
    "    response.delete_cookie(\n"
    "        OAUTH_STATE_COOKIE,\n"
    '        path=params["path"],\n'
    '        httponly=params["httponly"],\n'
    '        secure=params["secure"],\n'
    '        samesite=params["samesite"],\n'
    "    )"
)

_OAUTH_REDIRECT_LITERAL_MATCH_BLOCK = (
    "    if any(ch in redirect_uri for ch in _REDIRECT_PATTERN_CHARS):\n"
    "        return False\n"
    "    for candidate in _provider_allowed_redirects(provider, oauth_config):\n"
    "        if not isinstance(candidate, str) or any(\n"
    "            ch in candidate for ch in _REDIRECT_PATTERN_CHARS\n"
    "        ):\n"
    "            continue\n"
    "        # Deliberately plain equality: no rstrip(), no normalisation at all.\n"
    "        if candidate == redirect_uri:\n"
    "            return True\n"
    "    return False"
)

_OAUTH_APP_REDIRECT_CANONICAL_BLOCK = (
    "    canonical = require_oauth_redirect_url(text, field=field)\n"
    "    if canonical != text:\n"
    '        raise HardeningViolation(f"{field} must be an exact registered URI")\n'
    "    if _contains_redirect_pattern(text):\n"
    '        raise HardeningViolation(f"{field} must not contain wildcard characters")'
)

_OAUTH_APP_REDIRECT_LITERAL_MATCH_BLOCK = (
    "        if entry == text:\n"
    "            return text\n"
    '    raise HardeningViolation(f"{field} is not registered")'
)

_OAUTH_STATE_REDIRECT_BINDING_BLOCK = (
    "        # Verify redirect URI matches\n"
    '        if row["redirect_uri"] != redirect_uri:\n'
    "            logger.warning(f\"OAuth state redirect_uri mismatch for state {row['id']}\")\n"
    '            return False, None, "State token redirect URI mismatch"'
)

_OAUTH_CODE_REDIRECT_BINDING_BLOCK = (
    '        if code_record["redirect_uri"] != redirect_uri:\n'
    '            raise InvalidGrantError("Redirect URI mismatch")'
)


# --- N699 (c6ebc44b): artifact-management bot-token owner-confusion ---
# A bot token's user_id is its human owner. The require_user_token gate in each
# owner-scoped artifact mutating handler stops a narrow bot credential from
# wielding the owner's server/conversation authority through _authorize_scope
# and authoring the row as the owner. Pin the distinctive comment+call block in
# each file so a stale staged copy that drops the gate is caught by name.
_ARTIFACT_CRUD_BOT_TOKEN_OWNER_GUARD = (
    "    # A bot token's user_id is its human owner. Without this gate a narrow\n"
    "    # bot credential wields the owner's server/conversation authority through\n"
    "    # _authorize_scope and charges the authored row to the owner's identity.\n"
    "    # Match the emoji, sticker and soundboard management routes.\n"
    "    require_user_token(current_user)"
)

_ARTIFACT_UPLOAD_BOT_TOKEN_OWNER_GUARD = (
    "    # A bot token's user_id is its human owner. Without this gate a narrow\n"
    "    # bot credential wields the owner's server/conversation authority through\n"
    "    # _authorize_scope and authors the converted row as the owner.\n"
    "    require_user_token(current_user)"
)


# be19f4ca (N644 follow-up): logout re-checks the presented session bearer's
# IP/UA against the session's blind indexes before the revoking write. The
# `return False` fail-closed arms and the ip_index/ua_index SELECT distinguish
# this from the refresh/WS-ticket compares (which return None / raise).
_LOGOUT_CONTEXT_BINDING_SELECT = (
    "SELECT user_id, token_hash, ip_index, ua_index FROM auth_sessions WHERE id = ?"
)
_LOGOUT_CONTEXT_BINDING_COMPARE = (
    "                    current_ip_index = self.crypto.fast_blind_index(\n"
    "                        ip_address, \"ip_address\"\n"
    "                    )\n"
    "                    if not hmac.compare_digest(stored_ip_index, current_ip_index):\n"
    "                        return False\n"
    "                    if not user_agent:\n"
    "                        return False\n"
    "                    stored_ua_index = row.get(\"ua_index\")\n"
    "                    if not isinstance(stored_ua_index, str) or not stored_ua_index:\n"
    "                        return False\n"
    "                    current_ua_index = self._ua_index(user_agent)\n"
    "                    if not isinstance(current_ua_index, str) or not current_ua_index:\n"
    "                        return False\n"
    "                    if not hmac.compare_digest(stored_ua_index, current_ua_index):\n"
    "                        return False"
)
_LOGOUT_FACADE_THREADING = (
    "    return _get_auth_manager().get_instance().logout(token, ip_address, user_agent)"
)


COMMITTED_FIX_MARKERS: tuple[CommittedFixMarker, ...] = (
    CommittedFixMarker(
        key="n448-channel-detail-cache-key",
        path="src/api/routes/channels/crud.py",
        marker="key_builder=authenticated_cache_key(",
        why=(
            "N448: the cached channel-detail loader partitions its cache by "
            "credential; a stale staged copy drops key_builder and falls back "
            "to the default cache key"
        ),
    ),
    CommittedFixMarker(
        key="n448-server-channels-cache-key",
        path="src/api/routes/servers/channels.py",
        marker="key_builder=authenticated_cache_key(",
        why=(
            "N448 main: the server-channels listing cache is keyed by "
            "credential; stale copies silently restore the default cache key"
        ),
    ),
    CommittedFixMarker(
        key="n478-thread-messages-limit-clamp",
        path="src/core/threads/manager/messaging.py",
        marker="limit = _clamp_page_limit(limit)",
        why=(
            "N478: the thread message listing clamps LIMIT from above; a "
            "stale staged copy reverts to the bare max(0, limit) check and "
            "reopens unbounded-history reads"
        ),
    ),
    CommittedFixMarker(
        key="n478-mutual-suggestions-limit-clamp",
        path="src/core/relationships/manager/mutual.py",
        marker="limit = _clamp_page_limit(limit)",
        why=(
            "N478: the mutual/suggestions listing clamps LIMIT from above; a "
            "stale staged copy reverts to max(0, limit)"
        ),
    ),
    CommittedFixMarker(
        key="n459-server-route-structural-sweep",
        path="src/tests/security/test_server_route_id_parse_boundary.py",
        marker="def _iter_server_modules(",
        why=(
            "N459: the route-id boundary guard walks the whole server route "
            "tree structurally; a stale staged copy restores a curated file "
            "list and makes the sweep vacuous"
        ),
    ),
    # cd9bcf44 (private-index repair): three channel gates now DENY when the
    # servers module is unwired instead of skipping the backing-channel check.
    # All three files are still MM in the working tree with the pre-fix `return`
    # restored, so a peer staging a whole file would silently re-open the leak.
    # The marker is the distinctive fail-closed comment that exists only in the
    # repaired blob (absent from every stale working-tree copy).
    CommittedFixMarker(
        key="cd9bcf44-attachment-unwired-send-gate",
        path="src/api/routes/channels/attachments.py",
        marker="The backing-channel lookup cannot be evaluated without the servers",
        why=(
            "cd9bcf44: attachment upload fails closed when servers is unwired; "
            "a stale staged copy restores the bare `return` and lets any "
            "participant upload into a private-channel conversation-id alias "
            "whose messages.send/view grants were never checked"
        ),
    ),
    CommittedFixMarker(
        key="cd9bcf44-forward-unwired-send-gate",
        path="src/api/routes/features/forwarding.py",
        marker="The backing-channel lookup cannot be evaluated without the servers",
        why=(
            "cd9bcf44: message forwarding fails closed when servers is unwired; "
            "a stale staged copy restores the bare `return` and lets a member "
            "forward into a channel whose messages.send grant was never checked"
        ),
    ),
    CommittedFixMarker(
        key="cd9bcf44-edit-unwired-view-gate",
        path="src/api/routes/messages/messages_crud/edit.py",
        marker="The backing-channel lookup cannot be evaluated without the servers",
        why=(
            "cd9bcf44: message edit fails closed when servers is unwired; a "
            "stale staged copy restores the bare `return` and lets a member "
            "denied channels.view edit a private-channel message through the "
            "conversation-id alias"
        ),
    ),
    # --- Query-cache classifier sub-fixes (seven distinct fixes) ---
    # The committed counterparts of the working-tree meta-guard entries: these
    # pin the individual hardening steps inside _query_mutates_data so a peer
    # committing a stale/gutted execution.py is caught by name.
    CommittedFixMarker(
        key="query-cache-escape-string-pattern",
        path="src/core/database/core/execution.py",
        marker=r"\b[Ee]'(?:[^'\\]|\\.|'')*'",
        why=(
            "backslash-aware [Ee] escape-string alternative in _SQL_NON_CODE_RE; "
            "without it an escape-quoted quote pairs with a later quote and a "
            "stacked write separator is swallowed, so a write stays a 'read' "
            "and stale authz verdicts keep being served"
        ),
    ),
    CommittedFixMarker(
        key="query-cache-line-comment-cr-aware",
        path="src/core/database/core/execution.py",
        marker=r"--[^\n\r]*",
        why=(
            "the SQL line-comment alternative stops at both newline characters; "
            "a \\n-only variant lets a CR terminate the PostgreSQL comment early "
            "and exposes a stacked write separator the cache must see"
        ),
    ),
    CommittedFixMarker(
        key="query-cache-hash-not-comment",
        path="src/core/database/core/execution.py",
        marker=r"#[^\n]*",
        why=(
            "'#' must NOT be neutralised as a comment: on PostgreSQL it is the "
            "integer-XOR operator, so stripping a '#' tail would hide a stacked "
            "'; UPDATE ...' from the separator count. Scoped to the regex block "
            "because the same text appears in the adjacent rationale comment."
        ),
        absent=True,
        scope=("_SQL_NON_CODE_RE = re.compile(", "re.DOTALL,"),
    ),
    CommittedFixMarker(
        key="query-cache-explain-not-read-prefix",
        path="src/core/database/core/execution.py",
        marker='"EXPLAIN",',
        why=(
            "EXPLAIN must stay out of _QUERY_CACHE_READ_PREFIXES: PostgreSQL "
            "EXPLAIN ANALYZE executes its target, so a leading EXPLAIN would let "
            "a committed write keep the query cache serving a stale verdict"
        ),
        absent=True,
    ),
    CommittedFixMarker(
        key="query-cache-multiple-statement-check",
        path="src/core/database/core/execution.py",
        marker="def _has_multiple_statements(",
        why=(
            "counts top-level separators after neutralising literals, quoted "
            "bodies and comments, so a write stacked behind a read is treated "
            "as a write and the cache is invalidated"
        ),
    ),
    CommittedFixMarker(
        key="query-cache-dollar-quote-neutralization",
        path="src/core/database/core/execution.py",
        marker=r"\$\$.*?\$\$|\$(?P<dollar_tag>",
        why=(
            "dollar-quoted bodies ($$...$$ and $tag$...$tag$) are blanked as a "
            "unit so a lone quote inside one cannot pair with an outside quote "
            "and swallow a genuine stacked-write separator"
        ),
    ),
    # 3e35a337 / 6b4e443c: the WS-ticket exchange enforces the parent session's
    # IP/User-Agent binding in BOTH _verify_ws_ticket copies (TicketMixin's
    # active implementation and the LifecycleMixin mirror). Either file is
    # staged/modified in peers' working trees, so a whole-file stage can silently
    # drop one copy's binding check. Pin the constant-time ip_index/ua_index
    # compares in each copy independently: each entry is a normal positive
    # marker, so the parametrized synthetic-removal test proves non-vacuity for
    # it automatically.
    CommittedFixMarker(
        key="3e35a337-ws-ticket-ip-binding-tickets",
        path="src/core/auth/managers/session/tickets.py",
        marker=_WS_TICKET_IP_BINDING_COMPARE,
        why=(
            "3e35a337: TicketMixin._verify_ws_ticket compares the presented IP's "
            "blind index against the parent session's stored ip_index in "
            "constant time; a stale staged copy drops the check and lets a "
            "stolen ticket be exchanged from any host, laundering away the "
            "session binding the live verifier enforces"
        ),
    ),
    CommittedFixMarker(
        key="3e35a337-ws-ticket-ua-binding-tickets",
        path="src/core/auth/managers/session/tickets.py",
        marker=_WS_TICKET_UA_BINDING_COMPARE,
        why=(
            "3e35a337: TicketMixin._verify_ws_ticket compares the presented "
            "User-Agent's blind index against the stored ua_index in constant "
            "time; a stale staged copy drops the check and lets a stolen ticket "
            "be exchanged with an arbitrary User-Agent"
        ),
    ),
    CommittedFixMarker(
        key="6b4e443c-ws-ticket-ip-binding-lifecycle-mirror",
        path="src/core/auth/managers/session/lifecycle.py",
        marker=_WS_TICKET_IP_BINDING_COMPARE,
        why=(
            "6b4e443c: the LifecycleMixin mirror of _verify_ws_ticket must carry "
            "the same constant-time ip_index compare as TicketMixin so an MRO "
            "reorder or direct caller cannot bypass binding; a stale staged "
            "copy drops it and reopens the cross-host ticket exchange"
        ),
    ),
    CommittedFixMarker(
        key="6b4e443c-ws-ticket-ua-binding-lifecycle-mirror",
        path="src/core/auth/managers/session/lifecycle.py",
        marker=_WS_TICKET_UA_BINDING_COMPARE,
        why=(
            "6b4e443c: the LifecycleMixin mirror of _verify_ws_ticket must carry "
            "the same constant-time ua_index compare as TicketMixin; a stale "
            "staged copy drops it and lets the mirror accept a stolen ticket "
            "with an arbitrary User-Agent"
        ),
    ),
    # --- Targeted gateway socket closure on session revocation ---
    # Private-index repairs dc3b481e / c62071ac landed these closes at HEAD
    # while peer agents' working-tree/index copies of the same files are stale.
    # A peer staging a whole stale file would silently drop the server-side
    # close and leave revoked credentials usable on an already-authenticated
    # gateway socket (gateway auth happens only at IDENTIFY/RESUME).
    CommittedFixMarker(
        key="c62071ac-disable-2fa-session-socket-close",
        path="src/core/auth/managers/twofactor.py",
        marker=(
            'for row in to_close:\n'
            '            close_session_gateway_connections(\n'
            '                user_id, int(row["id"]), "Session revoked"\n'
            '            )'
        ),
        why=(
            "c62071ac: disable_2fa closes the sockets of the sessions it "
            "revokes (preserving the current session); a stale staged copy "
            "drops the close and leaves pre-2FA-trusted sessions usable"
        ),
    ),
    CommittedFixMarker(
        key="dc3b481e-logout-session-socket-close",
        path="src/core/auth/managers/session/lifecycle.py",
        marker=(
            'close_session_gateway_connections(\n'
            '                    row["user_id"], parsed["id"], "Session revoked"\n'
            '                )'
        ),
        why=(
            "dc3b481e: bearer-token logout closes its own session's socket; "
            "a stale staged copy leaves that socket authenticated after logout"
        ),
    ),
    CommittedFixMarker(
        key="dc3b481e-max-eviction-session-socket-close",
        path="src/core/auth/managers/session/lifecycle.py",
        marker=(
            'close_session_gateway_connections(\n'
            '                        user_id, evicted_id, "Session revoked"\n'
            '                    )'
        ),
        why=(
            "dc3b481e: max-sessions-per-user eviction closes the evicted "
            "session's socket; a stale staged copy leaves an evicted, revoked "
            "session still usable server-side"
        ),
    ),
    CommittedFixMarker(
        key="dc3b481e-bulk-revoke-session-socket-close",
        path="src/core/auth/managers/session/lifecycle.py",
        marker=(
            'for row in now_revoked:\n'
            '                close_session_gateway_connections('
        ),
        why=(
            "dc3b481e: revoke_sessions_bulk closes each revoked session's "
            "socket by id (keeping the except_current survivor); a stale "
            "staged copy restores a hint-only revoke with live sockets"
        ),
    ),
    CommittedFixMarker(
        key="dc3b481e-revoke-device-session-socket-close",
        path="src/core/auth/managers/device.py",
        marker=(
            'self._log_audit(AuditEventType.DEVICE_REVOKED, user_id, True)\n'
            '        for row in owned:\n'
            '            close_session_gateway_connections('
        ),
        why=(
            "dc3b481e: revoke_device closes the revoked device's session "
            "sockets without killing other devices; a stale staged copy "
            "leaves them authenticated"
        ),
    ),
    CommittedFixMarker(
        key="dc3b481e-untrust-device-session-socket-close",
        path="src/core/auth/managers/device.py",
        marker=(
            'self._log_audit(AuditEventType.DEVICE_UNTRUSTED, user_id, True)\n'
            '        for row in owned:\n'
            '            close_session_gateway_connections('
        ),
        why=(
            "dc3b481e: untrust_device closes the revoked fingerprint's session "
            "sockets without killing other devices; a stale staged copy "
            "leaves them authenticated"
        ),
    ),
    # --- Additional private-index fixes at risk of stale-staged-file revert ---
    # Each entry below pins the key fix of a commit that was landed through the
    # private index while a peer's working-tree/index copy of the same file is
    # still the pre-fix version.  The distinctive marker exists only in the
    # repaired HEAD blob, so staging a whole stale file silently reverts it.
    CommittedFixMarker(
        key="79e83f54-override-owner-id-type-confusion",
        path="src/core/servers/handlers/roles/overrides.py",
        marker='raise RoleHierarchyError("Cannot verify server owner identity")',
        why=(
            "79e83f54: the channel-override hierarchy gate rejects a "
            "type-confused server owner id (bool/str/float) instead of the "
            "authoritative int; a stale staged copy restores a loose == that "
            "lets a channels.manage holder deny the real owner's access"
        ),
    ),
    CommittedFixMarker(
        key="4acc2696-typing-viewer-id-type-confusion",
        path="src/api/websocket/handlers/presence.py",
        marker="if type(member_user_id) is not int:",
        why=(
            "4acc2696: the typing viewer audience drops malformed-but-coercible "
            "member ids before the int() laundering at the cache/dispatch "
            "boundary; a stale staged copy re-admits a principal that never "
            "proved channels.view to TYPING_START/STOP fan-out"
        ),
    ),
    CommittedFixMarker(
        key="bc7d08d9-dispatch-recipient-id-type-confusion",
        path="src/api/websocket/dispatcher.py",
        marker="user_ids = [uid for uid in user_ids if type(uid) is int]",
        why=(
            "bc7d08d9: the gateway fan-out boundary drops type-confused "
            "recipient ids instead of int()-laundering them; a stale staged "
            "copy leaks events to a wrong principal that never proved "
            "membership/view rights"
        ),
    ),
    CommittedFixMarker(
        key="98469c69-event-channel-audience-id-type-confusion",
        path="src/core/events/gateway_emit.py",
        marker="if type(uid) is not int:",
        why=(
            "98469c69: the channel-event audience resolver's final dedupe/cache "
            "step keeps only genuine int ids; a stale staged copy launders a "
            "type-confused id into a cached audience that never proved "
            "view/participation rights"
        ),
    ),
    CommittedFixMarker(
        key="98469c69-event-envelope-audience-id-type-confusion",
        path="src/core/events/manager.py",
        marker='"u": [uid for uid in recipients if type(uid) is int],',
        why=(
            "98469c69: the cross-worker envelope stamps only genuine int ids "
            "into the wire audience; a stale staged copy restores int() "
            "laundering and leaks a wrong principal into sibling workers"
        ),
    ),
    CommittedFixMarker(
        key="8cdcddb0-twofactor-no-debug-print",
        path="src/core/auth/managers/twofactor.py",
        marker="DBG manager complete_2fa",
        why=(
            "8cdcddb0: the 2FA completion path must not copy client IP/user-agent "
            "to stdout via a bare print; this absent-marker fails if a stale "
            "staged copy reintroduces the debug print"
        ),
        absent=True,
    ),
    CommittedFixMarker(
        key="b71b0d64-targeted-session-socket-close-method",
        path="src/api/websocket/dispatcher.py",
        marker="async def close_session_connections(",
        why=(
            "b71b0d64: the dispatcher gains a targeted close_session_connections "
            "helper bound to the verified auth_session_id so a single-session "
            "revoke closes only matching sockets; a stale staged copy drops it "
            "and forces a blanket close (or leaves revoked sockets live)"
        ),
    ),
    CommittedFixMarker(
        key="33cf9e7d-refresh-replay-session-socket-close",
        path="src/core/auth/managers/session/lifecycle.py",
        marker='row["user_id"], session_id, "Session revoked"',
        why=(
            "33cf9e7d: refresh-token replay revocation closes the single socket "
            "bound to the replayed session; a stale staged copy revokes the row "
            "but leaves the authenticated socket live server-side"
        ),
    ),
    # ded43f8e (N644, empty-reflog/CAS landing): refresh_session now re-checks
    # the presented refresh bearer's IP/UA context against the session's stored
    # blind indexes in constant time and fails closed BEFORE the atomic consume.
    # lifecycle.py is MM (stale pre-fix staged copy) in peers' working trees, so
    # a whole-file stage would silently drop this gate and let a stolen refresh
    # bearer used from another host force rotation and trip replay revocation
    # for the victim. The distinctive refresh_session compare pins that fix.
    CommittedFixMarker(
        key="ded43f8e-refresh-session-ip-ua-context-binding",
        path="src/core/auth/managers/session/lifecycle.py",
        marker=_REFRESH_SESSION_CONTEXT_BINDING,
        why=(
            "ded43f8e: refresh_session's constant-time IP/UA context check "
            "fails closed before the atomic consume, so a refresh bearer "
            "stolen and presented from another host cannot force rotation and "
            "retire the victim's current credential via replay revocation; a "
            "stale staged copy of lifecycle.py drops the compares and reopens "
            "the cross-host refresh-rotation availability attack"
        ),
    ),
    # bb824009 (private-index repair): the REST logout/revoke-all and 2FA-enable
    # paths close live gateway sockets.  All three files are stale in a peer's
    # working tree/index, so a whole-file stage would re-open authenticated
    # sockets after a credential transition.
    CommittedFixMarker(
        key="bb824009-logout-closes-user-sockets",
        path="src/api/routes/auth/sessions.py",
        marker='close_user_gateway_connections(current_user.user_id, "Logged out")',
        why=(
            "bb824009: an explicit REST logout closes live gateway sockets; a "
            "cookie clear alone leaves the socket authenticated server-side"
        ),
    ),
    CommittedFixMarker(
        key="bb824009-revoke-all-closes-user-sockets",
        path="src/api/routes/auth/sessions.py",
        marker='current_user.user_id, "All sessions revoked"',
        why=(
            "bb824009: revoke-all sessions closes live gateway sockets; a stale "
            "staged copy revokes the rows but keeps the user's sockets live"
        ),
    ),
    CommittedFixMarker(
        key="bb824009-2fa-enable-closes-user-sockets",
        path="src/core/auth/managers/twofactor.py",
        marker='close_user_gateway_connections(user_id, "Two-factor enabled")',
        why=(
            "bb824009: enabling 2FA closes pre-2FA sockets (fail-closed because "
            "the connection does not record the auth-session id); a stale staged "
            "copy leaves pre-2FA-trusted sockets authenticated"
        ),
    ),
    # --- Session-id rotation at 2FA privilege transitions (024ad8a4) ---
    # The HTTP 2FA enable/disable routes rotate the pre-transition session id
    # and re-issue the HttpOnly cookie. The manager still preserves
    # except_session_id for non-HTTP callers, so only the route wiring closes
    # the fixation gap; a stale staged route file silently reopens it.
    CommittedFixMarker(
        key="024ad8a4-confirm-2fa-route-session-rotation",
        path="src/api/routes/auth/two_factor.py",
        marker="# Privilege transition (password-only -> 2FA): rotate the session id.",
        why=(
            "024ad8a4: confirm_2fa_setup rotates the pre-2FA session id and "
            "re-issues the cookie; a stale staged copy keeps the fixated id "
            "valid after the second factor is enabled"
        ),
    ),
    CommittedFixMarker(
        key="024ad8a4-disable-2fa-route-session-rotation",
        path="src/api/routes/auth/two_factor.py",
        marker="# Disabling 2FA is also a privilege transition: the preserved current",
        why=(
            "024ad8a4: disable_2fa rotates the pre-transition session id and "
            "re-issues the cookie; a stale staged copy keeps the old id alive "
            "after the privilege switch"
        ),
    ),
    CommittedFixMarker(
        key="024ad8a4-2fa-rotation-cookie-reissue",
        path="src/api/routes/auth/two_factor.py",
        marker="set_session_cookie(response, rotated.token, session_expiry_seconds(rotated))",
        why=(
            "024ad8a4: both 2FA transitions hand the rotated credential back as "
            "a fresh HttpOnly Set-Cookie; a stale staged copy rotates the id "
            "without updating the browser cookie and breaks the session"
        ),
    ),
    CommittedFixMarker(
        key="024ad8a4-session-rotation-primitive",
        path="src/core/auth/managers/session/rotation.py",
        marker="def rotate_session_id(self, user_id: int, old_session_id: int) -> Session:",
        why=(
            "024ad8a4: RotationMixin.rotate_session_id revokes the old session "
            "(closing its socket) and mints a new id+secret; a stale staged "
            "copy drops the primitive the 2FA routes depend on"
        ),
    ),
    # --- Private-index audit lane (empty-reflog commit-tree/CAS landings) ---
    # Each entry below pins a security fix that was landed through the private
    # index and is therefore present at HEAD while peer working-tree/index
    # copies of the same file remain stale (M/MM). Staging a whole stale file
    # would silently re-open the finding; these markers name distinctive
    # repaired text so that revert is caught by name.
    CommittedFixMarker(
        key="08f4c144-webhook-signature-replay-single-use",
        path="src/core/applications/manager/webhook.py",
        marker="_claim_replay_key(",
        why=(
            "08f4c144: a captured validly signed application webhook must be "
            "single-use; the timestamp-skew window alone permits a replay "
            "inside the window. The repair adds an atomic signature-keyed "
            "claim; a stale staged copy drops it and lets a replayed signed "
            "webhook be accepted twice"
        ),
    ),
    CommittedFixMarker(
        key="2a98c5ad-voice-audience-unwired-fail-open",
        path="src/api/websocket/handlers/voice.py",
        marker='getattr(relationships, "_setup_complete", None) is False',
        why=(
            "2a98c5ad: when the optional relationships module is unwired the "
            "voice audience must be KEPT (no block data exists to apply), "
            "matching the DM/typing helpers; a stale staged copy treats the "
            "missing source as a failed lookup and drops the whole voice "
            "audience (self-DoS on voice state/speaking fan-out)"
        ),
    ),
    CommittedFixMarker(
        key="989482b3-voice-audience-two-way-block-gate",
        path="src/api/websocket/handlers/voice.py",
        marker="def _exclude_blocked_voice_recipients(",
        why=(
            "989482b3: voice state/speaking frames carry an explicit recipient "
            "list that bypasses the event router's DM block filter; the repair "
            "adds a two-way-block gate that fails closed on lookup error. A "
            "stale staged copy lets a blocked pair keep receiving the "
            "speaker's voice state and speaking frames"
        ),
    ),
    CommittedFixMarker(
        key="2bcfd736-dm-block-positive-audience-strict-int",
        path="src/core/events/gateway_emit.py",
        marker="this loop builds the POSITIVE audience",
        why=(
            "2bcfd736: the DM block-filter POSITIVE participant audience must "
            "use strict type() int checks; tolerant _as_id() coercion belongs "
            "only to block-set comparison keys. A stale staged copy launders "
            "'05'/7.9/True into principals that never proved participation, "
            "before the final strict guard can catch them"
        ),
    ),
    CommittedFixMarker(
        key="40e4cdd5-presence-audience-block-lookup-fail-closed",
        path="src/api/routes/presence.py",
        marker="FAIL CLOSED: get_all_blocked_ids is the authoritative",
        why=(
            "40e4cdd5: _get_presence_targets must withhold the whole audience "
            "when the authoritative two-way block lookup fails; a stale "
            "staged copy broadcasts the subject's real presence to users who "
            "blocked them (or whom they blocked)"
        ),
    ),
    CommittedFixMarker(
        key="40e4cdd5-offline-presence-block-lookup-fail-closed",
        path="src/api/websocket/gateway.py",
        marker="FAIL CLOSED: the two-way block set is authoritative",
        why=(
            "40e4cdd5: the disconnect/offline presence dispatcher builds its "
            "own audience and must also fail closed on a block-lookup error; "
            "a stale staged copy delivers offline presence to a blocked pair"
        ),
    ),
    CommittedFixMarker(
        key="c266051f-onboarding-whitelist-author-revalidation",
        path="src/core/servers/manager/onboarding_ops.py",
        marker="Onboarding role whitelist has no recorded author",
        why=(
            "c266051f: the self-service completion path re-validates the "
            "whitelist author's CURRENT rank/grantability before bulk role "
            "grants; a stale staged copy lets a member self-assign elevated "
            "grants after a whitelisted role's permissions were edited"
        ),
    ),
    CommittedFixMarker(
        key="cc980f6f-user-search-server-scope-membership-gate",
        path="src/core/search/manager/user_search.py",
        marker="def _scope_allows_viewer(",
        why=(
            "cc980f6f: a server_id user-search scope is a visibility boundary, "
            "so the caller must be a member of that server; failing closed on "
            "malformed scope data. A stale staged copy turns "
            "/search/users?server_id=X into a roster-enumeration oracle"
        ),
    ),
    CommittedFixMarker(
        key="ced224a3-alert-moderator-audience-strict-int",
        path="src/core/automod/actions/alert.py",
        marker="mid for mid in moderator_ids if type(mid) is int",
        why=(
            "ced224a3: the ALERT_MODERATORS audience must keep only genuine "
            "int ids from the authoritative roster; a stale staged copy "
            "restores int() laundering and alerts the wrong principal, and "
            "the downstream preference gate never re-checks moderator authority"
        ),
    ),
    CommittedFixMarker(
        key="da39776a-api-config-verdict-deepcopy",
        path="src/api/config.py",
        marker="return deepcopy(cast(APIConfig, _api_config_snapshot.snapshot()))",
        why=(
            "da39776a: the process-wide APIConfig snapshot drives CORS and "
            "access-token gates, so reads must hand out a detached copy; a "
            "stale staged copy returns the shared mutable object and lets one "
            "caller widen the gate for every later request"
        ),
    ),
    CommittedFixMarker(
        key="da39776a-tier-limits-verdict-deepcopy",
        path="src/core/features/__init__.py",
        marker="def _copy_tier_limits(",
        why=(
            "da39776a: memoized TierLimits verdicts are shared process-wide, "
            "so both writes and reads must detach them; a stale staged copy "
            "lets a caller mutate a cached verdict and widen another "
            "principal's tier limits"
        ),
    ),
    CommittedFixMarker(
        key="ee7bab02-attachment-image-decode-size-ceiling",
        path="src/core/media/processing/images.py",
        marker="check_image_decode_limits(",
        why=(
            "ee7bab02: attachment image decodes must enforce the shared "
            "decoded-byte/frame ceiling before accepting a frame; a stale "
            "staged copy drops the check and reopens decompression-bomb DoS"
        ),
    ),
    CommittedFixMarker(
        key="fbc4a7be-admin-otp-ascii-canonicalization",
        path="src/core/admin/auth/otp_mixin.py",
        marker="def _is_canonical_totp_code(",
        why=(
            "fbc4a7be: pyotp NFKC-normalises its input, so a fullwidth "
            "homoglyph of a consumed TOTP code bypasses the single-use replay "
            "guard; the repair pins codes to canonical ASCII. A stale staged "
            "copy restores the homoglyph replay and mints a second admin session"
        ),
    ),
    # N658 (2e98203a): a replay key on only the presented code leaves the other
    # 2*window+1 adjacent codes that pyotp's valid_window accepts still live, so
    # two different still-valid codes could each authorize a separate step-up
    # inside one validity window. Claiming every accepted step atomically closes
    # that adjacent-code replay gap; a stale staged copy of totp.py drops the
    # window claim and silently reopens it.
    CommittedFixMarker(
        key="n658-totp-valid-window-replay",
        path="src/core/auth/totp.py",
        marker="_claim_totp_window",
        why=(
            "N658: verify_totp_code now claims every code accepted by "
            "valid_window (2*window+1 adjacent steps) so a second, different "
            "still-valid code cannot authorize another step-up inside one "
            "validity window; a stale staged copy of totp.py drops the window "
            "claim and reopens adjacent-code replay"
        ),
    ),
    # --- Stale-staged MM audit: committed fixes dropped by staged (index) copies ---
    # These three files are MM in a peer's working tree. Their staged blobs are
    # pre-fix while HEAD carries the fix; their working-tree copies re-add it.
    # A whole-index commit would therefore silently revert each repair. None of
    # these paths was pinned by any earlier committed-tree marker.
    CommittedFixMarker(
        key="6e80af56-audit-log-read-bot-token-owner-gate",
        path="src/api/routes/servers/audit_log.py",
        marker="    require_user_token(current_user)",
        why=(
            "6e80af56: the server audit-log read rejects bot credentials before "
            "the core audit read treats the bot owner's id as the acting "
            "moderator; a stale staged copy drops the require_user_token gate "
            "and lets a narrow-scope bot read any server's moderation history"
        ),
    ),
    CommittedFixMarker(
        key="9f6efef0-poll-update-guild-message-intent-gate",
        path="src/core/events/router.py",
        marker="    EventType.POLL_UPDATE: GatewayIntent.GUILD_MESSAGES,",
        why=(
            "9f6efef0: guild-scoped POLL_UPDATE is mapped to the message intent; "
            "a stale staged copy drops the map entry and the router's "
            "default-allow delivers poll activity to sockets with no messaging "
            "intent"
        ),
    ),
    CommittedFixMarker(
        key="9f6efef0-poll-update-dm-direct-message-intent-gate",
        path="src/core/events/router.py",
        marker="    EventType.POLL_UPDATE: GatewayIntent.DIRECT_MESSAGES,",
        why=(
            "9f6efef0: emit_poll_update stamps only a channel_id, so every poll "
            "update is DM-classified; a stale staged copy drops the DM map entry "
            "and discloses a private conversation's poll/message activity to "
            "connections that never requested DIRECT_MESSAGES"
        ),
    ),
    CommittedFixMarker(
        key="70cd4627-presence-audience-eviction-on-relationship-change",
        path="src/core/relationships/manager/helpers.py",
        marker="from src.core.events.router import clear_presence_audience_cache",
        why=(
            "70cd4627: relationship changes eagerly evict the EventRouter "
            "presence-audience cache; a stale staged copy drops the evict so a "
            "just-blocked user keeps receiving the blocker's real presence "
            "until the cache TTL lapses"
        ),
    ),
    # 42ee85e9 (private-index repair): the federation join manager logs through
    # a plain stdlib logger, so remote-supplied error text reaches the sink
    # without AppLogger's control-character filter and can forge extra
    # disk/SIEM log lines (CWE-117). The sink-level SanitizingFilter install is
    # the fix; a stale/gutted copy drops it and reopens the log-forging leak.
    CommittedFixMarker(
        key="42ee85e9-plexijoin-manager-log-sanitizing-filter",
        path="src/core/plexijoin/manager.py",
        marker="logger.addFilter(SanitizingFilter())",
        why=(
            "42ee85e9: the federation join manager logs remote-supplied error "
            "text through a plain logger with no SanitizingFilter, so a U+0085, "
            "U+2028/U+2029 or bidi control can forge extra disk/SIEM log lines "
            "(CWE-117); a stale staged copy drops the sink-level filter install "
            "and reopens the log-forging leak"
        ),
    ),
    # 46e545f2: move_role's CAS update carries the caller-observed position in
    # the WHERE clause and raises on a zero-row update. The MAIN working tree's
    # copy of roles/crud.py is stale (pre-fix, no position predicate); a peer
    # staging that whole file would silently drop the CAS and restore the
    # lost-update race where two concurrent moves clobber each other's reorder.
    CommittedFixMarker(
        key="46e545f2-move-role-stale-position-cas",
        path="src/core/servers/handlers/roles/crud.py",
        marker="Role changed concurrently, retry",
        why=(
            "46e545f2: move_role guards against stale-position TOCTOU with a "
            "position-scoped CAS update that raises when zero rows change; a "
            "stale working-tree copy of roles/crud.py restores the unchecked "
            "UPDATE and lets a concurrent move silently revert another's "
            "reordering, bypassing the hierarchy revalidation performed "
            "against the outdated position"
        ),
    ),
    # 2FA atomic counter (332e722c): the Valkey-backed 2FA attempt limiter
    # fuses INCR and the first-hit EXPIRE into one Lua script. The MAIN working
    # tree's copy of twofactor.py is contested/stale (a staged diff reverts the
    # atomic script, gateway socket revocation, and re-adds a debug print), so
    # a whole-file stage on top of HEAD would silently drop the atomic script
    # and restore the no-TTL window / permanent 2FA lockout. aed91be5 dropped
    # this pin; restore it so a stale staged copy cannot drop the fix.
    CommittedFixMarker(
        key="2fa-attempt-counter-atomic-incr-expire",
        path="src/core/auth/managers/twofactor.py",
        marker="_2FA_INCR_EXPIRE_SCRIPT",
        why=(
            "332e722c: atomic INCR + first-hit EXPIRE for the 2FA attempt "
            "counter; the contested/stale working-tree copy of twofactor.py "
            "would restore the no-TTL window and permanent 2FA lockout"
        ),
    ),
    # N663 (4986e8a9): recipient DM privacy is re-checked at send time. Every
    # user-facing send path (REST, forward, scheduled dispatch, reaction
    # mentions) funnels through send_message, so a stale working-tree copy of
    # send.py that drops this call silently reopens the leak where a party to
    # an already-open DM keeps writing after the recipient set
    # allow_dms_from=none (or without ever proving friendship).
    CommittedFixMarker(
        key="4986e8a9-dm-recipient-privacy-send-gate",
        path="src/core/messaging/services/message/send.py",
        marker="_enforce_dm_recipient_privacy",
        why=(
            "N663: send_message re-checks every single-DM recipient's "
            "allow_dms_from setting at send time and fails closed on an "
            "unreadable conversation/settings lookup or a friends-only "
            "recipient with no relationships source; a stale staged copy of "
            "send.py drops the gate and lets a party to an already-open DM "
            "keep writing after the recipient switched dms to 'none' (or "
            "without ever proving friendship)"
        ),
    ),
    # 499bd649: the DM block gate also covers GROUP rows that are group DMs;
    # only server-channel GROUP rows (carrying a server_id) stay exempt. A
    # stale working-tree copy of send.py restores the early return for every
    # non-DM type, so a pair already co-resident in a group DM can keep
    # delivering after a block.
    CommittedFixMarker(
        key="499bd649-group-dm-send-block-gate",
        path="src/core/messaging/services/message/send.py",
        marker=(
            "            metadata = self._conversation_repo._json_loads("
            'conv_row.get("metadata"))\n'
            '            if isinstance(metadata, dict) and metadata.get("server_id"):'
        ),
        why=(
            "499bd649: _enforce_dm_block_rules now treats GROUP rows as group "
            "DMs (exempting only server channels that carry a server_id); a "
            "stale staged copy of send.py restores the early return for every "
            "non-DM type and lets a blocked pair already co-resident in a "
            "group DM keep delivering messages into it"
        ),
    ),
    # --- OAuth invariant pins (e85eab23 / N654 / N689) ---
    # The state cookie, the literal redirect allow-list and the
    # state/code -> redirect_uri bindings are what stop an attacker swapping
    # the callback target or replaying one state across callbacks. Every entry
    # is a positive marker, so the parametrized
    # test_synthetic_reverted_blob_is_flagged proves its non-vacuity.
    CommittedFixMarker(
        key="e85eab23-oauth-state-cookie-issuance-attributes",
        path="src/api/routes/auth/oauth.py",
        marker=_OAUTH_STATE_COOKIE_ISSUANCE_BLOCK,
        why=(
            "e85eab23: the OAuth state cookie is issued HttpOnly/Secure/"
            "SameSite=Lax on the OAuth-scoped path; the clear must mirror those "
            "exact attributes or the browser keeps the real Secure cookie"
        ),
    ),
    CommittedFixMarker(
        key="e85eab23-oauth-state-cookie-clear-attributes",
        path="src/api/routes/auth/oauth.py",
        marker=_OAUTH_STATE_COOKIE_CLEAR_BLOCK,
        why=(
            "e85eab23: _clear_oauth_state_cookie deletes the state cookie with "
            "path/httponly/secure/samesite copied from issuance; a stale staged "
            "copy restores the bare delete_cookie and both fails to clear the "
            "real cookie and emits an unprotected Set-Cookie"
        ),
    ),
    CommittedFixMarker(
        key="oauth-provider-redirect-pattern-chars-rejected",
        path="src/api/routes/auth/oauth.py",
        marker='_REDIRECT_PATTERN_CHARS = frozenset("*[]{}()|\\\\^$+")',
        why=(
            "the provider callback allow-list treats wildcard/regex characters "
            "as data, never patterns; dropping the metacharacter set turns the "
            "literal allow-list into an open redirector / code-interception "
            "primitive"
        ),
    ),
    CommittedFixMarker(
        key="oauth-provider-redirect-literal-equality",
        path="src/api/routes/auth/oauth.py",
        marker=_OAUTH_REDIRECT_LITERAL_MATCH_BLOCK,
        why=(
            "_redirect_uri_is_allowed rejects pattern characters and compares "
            "the candidate with plain == (no rstrip/case folding); a stale "
            "staged copy that folds or prefixes the URI accepts a look-alike "
            "callback and hands the authorization code to it"
        ),
    ),
    CommittedFixMarker(
        key="oauth-app-redirect-canonical-no-normalisation",
        path="src/core/applications/validation.py",
        marker=_OAUTH_APP_REDIRECT_CANONICAL_BLOCK,
        why=(
            "require_exact_redirect_match rejects any canonicalisation "
            "(canonical != text) and wildcard entries; without it a normalised "
            "look-alike callback passes the application allow-list"
        ),
    ),
    CommittedFixMarker(
        key="oauth-app-redirect-literal-equality",
        path="src/core/applications/validation.py",
        marker=_OAUTH_APP_REDIRECT_LITERAL_MATCH_BLOCK,
        why=(
            "require_exact_redirect_match returns only on literal entry == "
            "text and otherwise raises; a stale staged copy using "
            "startswith/substring matching reopens the open-redirector"
        ),
    ),
    CommittedFixMarker(
        key="oauth-state-redirect-uri-binding",
        path="src/core/auth/oauth/state.py",
        marker=_OAUTH_STATE_REDIRECT_BINDING_BLOCK,
        why=(
            "verify_state checks row[\"redirect_uri\"] != redirect_uri before "
            "the atomic consume, binding the state to the exact callback that "
            "minted it; a stale staged copy drops the check and lets a valid "
            "state be replayed against a different redirect_uri"
        ),
    ),
    CommittedFixMarker(
        key="oauth-app-code-redirect-uri-binding",
        path="src/core/applications/oauth/flows.py",
        marker=_OAUTH_CODE_REDIRECT_BINDING_BLOCK,
        why=(
            "the application authorization-code exchange rejects a code whose "
            "recorded redirect_uri differs from the presented one; without it "
            "a code issued for one callback is redeemable from another"
        ),
    ),
    # --- N699 (c6ebc44b): artifact-management bot-token owner-confusion ---
    # One pin per artifact mutating-handler file. Each marker is the distinctive
    # comment + require_user_token block added by c6ebc44b, so a stale staged
    # copy of either file that drops the gate is caught by name.
    CommittedFixMarker(
        key="c6ebc44b-artifact-crud-bot-token-owner-guard",
        path="src/api/routes/artifacts/routes/crud.py",
        marker=_ARTIFACT_CRUD_BOT_TOKEN_OWNER_GUARD,
        why=(
            "N699: create/update/delete_artifact reject bot credentials before "
            "_authorize_scope treats the bot owner's user_id as the acting "
            "member and before manager.create authors the row as the owner; a "
            "stale staged copy of crud.py drops the require_user_token gate and "
            "lets a narrow bot credential wield the owner's server/conversation "
            "authority"
        ),
    ),
    CommittedFixMarker(
        key="c6ebc44b-artifact-upload-bot-token-owner-guard",
        path="src/api/routes/artifacts/routes/uploads.py",
        marker=_ARTIFACT_UPLOAD_BOT_TOKEN_OWNER_GUARD,
        why=(
            "N699: _convert_upload_impl rejects bot credentials before "
            "_authorize_scope and manager.create author the converted artifact "
            "as the bot's human owner; a stale staged copy of uploads.py drops "
            "the require_user_token gate and reopens the owner-confusion"
        ),
    ),
    # cdeda33b carried both fixes in one commit (commit --only stages whole
    # paths): N700's under-lock rank/position re-assert and N642's under-lock
    # grantability/permission re-check. Pin them independently so dropping
    # either one from the committed assignments.py blob fails the guard.
    CommittedFixMarker(
        key="cdeda33b-role-assignment-grantability-recheck",
        path="src/core/servers/handlers/roles/assignments.py",
        marker=_ROLE_ASSIGNMENT_GRANTABILITY_RECHECK,
        why=(
            "N642: assign_role re-reads the role permission payload under the "
            "142 -> 143 locks (deleted = 0) and re-runs validate_permission_dict "
            "+ check_permissions_grantable before the insert, failing closed on "
            "a missing/malformed row; a stale copy drops the re-check and lets a "
            "widened role (administrator) be granted on a stale pre-lock decision"
        ),
    ),
    CommittedFixMarker(
        key="cdeda33b-role-assignment-rank-recheck",
        path="src/core/servers/handlers/roles/assignments.py",
        marker=_ROLE_ASSIGNMENT_RANK_RECHECK,
        why=(
            "N700: assign_role re-reads the role position under the same locks "
            "(deleted = 0) and re-asserts can_manage_role + _guard_member_target, "
            "failing closed on a missing row; a stale copy drops the re-check so "
            "a role promoted in the check->write gap is attached at/above the "
            "editor's rank and lifts the grantee"
        ),
    ),
    # 627accb2 (N690): a server channel's linked conversation_id used in a send
    # URL resolves to no channel row, so the REST ``if server_id:`` block -- the
    # only place that ran automod.check_message_fast -- was skipped entirely: a
    # moderation bypass reachable by addressing the channel through its alias.
    # The repair adds _enforce_alias_automod in base.py (reverse-map the alias,
    # run the same fast gate against the backing channel, fail closed) and calls
    # it from send.py. Both files are stale-stage prone, so pin the definition
    # and the call site independently: a peer committing a pre-fix whole-file
    # copy of either one would silently re-open the alias moderation bypass.
    CommittedFixMarker(
        key="627accb2-alias-automod-enforcement",
        path="src/api/routes/messages/messages_crud/base.py",
        marker=(
            "    def _enforce_alias_automod(\n"
            "        self,\n"
            "        addressed_id: int,\n"
            "        content: str,\n"
        ),
        why=(
            "627accb2: MessagesCRUDBase._enforce_alias_automod reverse-maps a "
            "conversation-id alias to its backing server channel and runs "
            "automod.check_message_fast there, failing closed when the alias "
            "cannot be resolved or the gate cannot be evaluated; without it a "
            "server-channel conversation-id alias skips the only automod gate "
            "on the REST send route and publishes unchecked content. A stale "
            "staged copy of base.py drops the method and reopens that "
            "alias-moderation-bypass."
        ),
    ),
    CommittedFixMarker(
        key="627accb2-alias-automod-send-call",
        path="src/api/routes/messages/messages_crud/send.py",
        marker=(
            "                self._enforce_alias_automod(\n"
            "                    cid,\n"
            "                    automod_content,\n"
        ),
        why=(
            "627accb2: the DM/conversation fallback in send_message calls "
            "_enforce_alias_automod so a server-channel conversation-id alias "
            "(which _resolve_server_id cannot see as a channel) still gets the "
            "fast moderation gate; a genuine DM has no backing channel and is "
            "left to participation + DM spam protection. A stale staged copy "
            "of send.py drops the call and silently restores the "
            "alias-moderation-bypass even though the base.py definition remains."
        ),
    ),
    # be19f4ca: the manager-level logout consumes a session bearer; with
    # token_binding on it must re-check the presented context before revoking,
    # else a bearer stolen from another host can log the victim out. Both files
    # are MM with stale pre-fix staged copies, so whole-file stages are caught.
    CommittedFixMarker(
        key="be19f4ca-logout-context-binding",
        path="src/core/auth/managers/session/lifecycle.py",
        marker=_LOGOUT_CONTEXT_BINDING_COMPARE,
        why=(
            "be19f4ca: logout re-checks the presented IP/UA against the "
            "session's blind indexes and fails closed before the revoke; a "
            "stale staged copy of lifecycle.py drops the check and lets a "
            "stale-context bearer revoke a session it cannot authenticate"
        ),
    ),
    CommittedFixMarker(
        key="be19f4ca-logout-facade-context-threading",
        path="src/core/auth/sessions.py",
        marker=_LOGOUT_FACADE_THREADING,
        why=(
            "be19f4ca: the public logout wrapper threads ip_address/user_agent "
            "to the manager, matching refresh_session; a stale staged copy "
            "drops the context and makes the manager gate unreachable"
        ),
    ),
)


def _git(*args: str, cwd: Path = _REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run, then pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


# Fine-grained query-cache classifier sub-fixes pinned in the committed blob.
QUERY_CACHE_COMMITTED_KEYS: frozenset[str] = frozenset(
    {
        "query-cache-escape-string-pattern",
        "query-cache-line-comment-cr-aware",
        "query-cache-hash-not-comment",
        "query-cache-explain-not-read-prefix",
        "query-cache-multiple-statement-check",
        "query-cache-dollar-quote-neutralization",
    }
)

# Private-index fixes pinned by this audit.  Kept explicit so an entry can never
# be dropped from the registry without this companion check failing.
PRIVATE_INDEX_PINNED_KEYS: frozenset[str] = frozenset(
    {
        "79e83f54-override-owner-id-type-confusion",
        "4acc2696-typing-viewer-id-type-confusion",
        "bc7d08d9-dispatch-recipient-id-type-confusion",
        "98469c69-event-channel-audience-id-type-confusion",
        "98469c69-event-envelope-audience-id-type-confusion",
        "8cdcddb0-twofactor-no-debug-print",
        "b71b0d64-targeted-session-socket-close-method",
        "33cf9e7d-refresh-replay-session-socket-close",
        "ded43f8e-refresh-session-ip-ua-context-binding",
        "be19f4ca-logout-context-binding",
        "be19f4ca-logout-facade-context-threading",
        "bb824009-logout-closes-user-sockets",
        "bb824009-revoke-all-closes-user-sockets",
        "bb824009-2fa-enable-closes-user-sockets",
        "024ad8a4-confirm-2fa-route-session-rotation",
        "024ad8a4-disable-2fa-route-session-rotation",
        "024ad8a4-2fa-rotation-cookie-reissue",
        "024ad8a4-session-rotation-primitive",
        "08f4c144-webhook-signature-replay-single-use",
        "2a98c5ad-voice-audience-unwired-fail-open",
        "989482b3-voice-audience-two-way-block-gate",
        "2bcfd736-dm-block-positive-audience-strict-int",
        "40e4cdd5-presence-audience-block-lookup-fail-closed",
        "40e4cdd5-offline-presence-block-lookup-fail-closed",
        "c266051f-onboarding-whitelist-author-revalidation",
        "cc980f6f-user-search-server-scope-membership-gate",
        "ced224a3-alert-moderator-audience-strict-int",
        "da39776a-api-config-verdict-deepcopy",
        "da39776a-tier-limits-verdict-deepcopy",
        "ee7bab02-attachment-image-decode-size-ceiling",
        "fbc4a7be-admin-otp-ascii-canonicalization",
        "42ee85e9-plexijoin-manager-log-sanitizing-filter",
    }
)

# Stale-staged MM audit (this guard-hardening lane): committed security fixes
# that a peer's staged (index) copy of an MM file drops while the working tree
# still carries them. Kept as an explicit set so dropping any one of these
# entries without updating the companion non-vacuity test fails loudly.
STALE_STAGED_REVERT_KEYS: frozenset[str] = frozenset(
    {
        "6e80af56-audit-log-read-bot-token-owner-gate",
        "9f6efef0-poll-update-guild-message-intent-gate",
        "9f6efef0-poll-update-dm-direct-message-intent-gate",
        "70cd4627-presence-audience-eviction-on-relationship-change",
    }
)

# WS-ticket session-binding pins (3e35a337 / 6b4e443c). Kept explicit so none
# of the four per-copy/per-field pins can be dropped from the registry without
# this companion check failing.
WS_TICKET_BINDING_KEYS: frozenset[str] = frozenset(
    {
        "3e35a337-ws-ticket-ip-binding-tickets",
        "3e35a337-ws-ticket-ua-binding-tickets",
        "6b4e443c-ws-ticket-ip-binding-lifecycle-mirror",
        "6b4e443c-ws-ticket-ua-binding-lifecycle-mirror",
    }
)


# OAuth invariant pins from the N689/N654 follow-up. Kept explicit so a later
# edit cannot quietly drop one of them and leave only a partial guard. All are
# positive markers; non-vacuity comes from both the parametrized
# synthetic-removal test and the live-HEAD check below.
OAUTH_INVARIANT_PIN_KEYS: frozenset[str] = frozenset(
    {
        "e85eab23-oauth-state-cookie-issuance-attributes",
        "e85eab23-oauth-state-cookie-clear-attributes",
        "oauth-provider-redirect-pattern-chars-rejected",
        "oauth-provider-redirect-literal-equality",
        "oauth-app-redirect-canonical-no-normalisation",
        "oauth-app-redirect-literal-equality",
        "oauth-state-redirect-uri-binding",
        "oauth-app-code-redirect-uri-binding",
    }
)


# N699 (c6ebc44b) artifact bot-token owner-confusion pins. Kept explicit so
# neither of the two per-file pins can be dropped without this check failing.
N699_ARTIFACT_BOT_TOKEN_PIN_KEYS: frozenset[str] = frozenset(
    {
        "c6ebc44b-artifact-crud-bot-token-owner-guard",
        "c6ebc44b-artifact-upload-bot-token-owner-guard",
    }
)


# N690 (627accb2) alias-automod pins. Kept explicit so neither the base.py
# definition nor the send.py call-site pin can be dropped without this check
# failing. Both are positive markers; non-vacuity comes from the guard's
# parametrized synthetic-removal test plus the live-HEAD tie below.
ALIAS_AUTOMOD_PIN_KEYS: frozenset[str] = frozenset(
    {
        "627accb2-alias-automod-enforcement",
        "627accb2-alias-automod-send-call",
    }
)


def _make_blob_reader(commit: str, repo_root: Path = _REPO_ROOT):
    """Return a reader that fetches ``<commit>:<path>`` with ``git show``."""

    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}", cwd=repo_root)
        if proc.returncode != 0:
            raise BlobMissing(path, proc.stderr.strip())
        return proc.stdout

    return read


def _scoped_text(text: str, record: CommittedFixMarker) -> str | None:
    """Return only the region between ``record.scope`` anchors, or ``None``.

    ``None`` is treated as a failure so a missing scope can never make a
    negative assertion pass vacuously.
    """
    if record.scope is None:
        return text
    start_anchor, end_anchor = record.scope
    start = text.find(start_anchor)
    if start < 0:
        return None
    end = text.find(end_anchor, start + len(start_anchor))
    if end < 0:
        return None
    return text[start:end]


def _evaluate(record: CommittedFixMarker, reader) -> tuple[bool, str]:
    """Return ``(present, detail)`` using the injectable ``reader``.

    The reader is injectable so the self-tests can serve synthetic committed
    blobs (marker removed/reintroduced, file deleted, file relocated) without
    touching the real repository.  For ``absent`` records the polarity is
    inverted: the entry passes only while the vulnerable pattern is NOT found.
    """
    checked: list[str] = []
    for rel in record.candidates:
        try:
            text = reader(rel)
        except BlobMissing as exc:
            checked.append(f"{rel} (missing from commit: {exc.reason})")
            continue
        except (OSError, UnicodeDecodeError) as exc:
            checked.append(f"{rel} (unreadable: {exc.__class__.__name__})")
            continue
        searched = _scoped_text(text, record)
        if searched is None:
            checked.append(f"{rel} (scope {record.scope!r} not found)")
            continue
        found = record.marker in searched
        if record.absent:
            if not found:
                return True, (
                    f"absent marker {record.marker} for {record.key!r} confirmed "
                    f"absent in committed blob {rel!r}"
                )
            checked.append(f"{rel} (VULNERABLE pattern {record.marker} present)")
        else:
            if found:
                return True, (
                    f"marker {record.marker} for {record.key!r} present in "
                    f"committed blob {rel!r}"
                )
            checked.append(rel)

    if record.absent:
        location = f"must be absent in committed blob {record.path!r}"
    else:
        location = f"expected in committed blob {record.path!r}"
    if record.successors:
        location += f" or documented successor(s) {record.successors!r}"
    if record.absent:
        detail = (
            f"COMMITTED SECURITY FIX MARKER VIOLATED: {record.key!r} -- "
            f"vulnerable pattern {record.marker} was REINTRODUCED ({location}); "
            "checked: " + ", ".join(checked) + f". Purpose: {record.why}"
        )
    else:
        detail = (
            f"COMMITTED SECURITY FIX MARKER MISSING: {record.key!r} -- "
            f"{record.marker} not found ({location}); checked: "
            + ", ".join(checked)
            + f". Purpose: {record.why}"
        )
    return False, detail


@pytest.mark.parametrize(
    "record", COMMITTED_FIX_MARKERS, ids=[r.key for r in COMMITTED_FIX_MARKERS]
)
def test_committed_fix_marker_present(
    record: CommittedFixMarker, commit_sha: str
) -> None:
    """Every documented high-risk fix is present in the committed tree."""
    present, detail = _evaluate(record, _make_blob_reader(commit_sha))
    assert present, detail


def test_registry_is_documentable_and_unique() -> None:
    """The committed-tree registry is well-formed (no empty/duplicate rows)."""
    keys = [r.key for r in COMMITTED_FIX_MARKERS]
    assert keys, "registry must not be empty"
    assert len(keys) == len(set(keys)), "duplicate fix keys in registry"
    for record in COMMITTED_FIX_MARKERS:
        assert record.marker, f"empty marker for {record.key!r}"
        assert record.path.startswith("src/"), f"bad path for {record.key!r}"
        assert record.why, f"undocumented purpose for {record.key!r}"
        # These are the contested repairs: silently tolerating removal would
        # defeat the whole point of the guard, so no successors are allowed.
        assert not record.successors, f"undocumented removal allowed for {record.key!r}"


def test_registry_has_no_orphaned_marker_entries() -> None:
    """Structural invariant: every module-level marker sits in the registry tuple.

    This heavily-contested file has repeatedly had a ``CommittedFixMarker(...)``
    appended just OUTSIDE the ``COMMITTED_FIX_MARKERS`` tuple. Such an entry is
    silently unguarded -- the parametrized presence/non-vacuity tests only see
    the tuple -- and, while being pasted, produced transient IndentationErrors.
    Parse this file's own AST and fail if any module-scope marker construction
    lives outside the registry tuple, or if two registry keys collide. Synthetic
    fixtures created *inside* test functions are intentionally exempt.
    """
    import ast

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(Path(__file__)))
    registry = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", "") == "COMMITTED_FIX_MARKERS"
        ),
        None,
    )
    assert registry is not None, "COMMITTED_FIX_MARKERS assignment not found"
    assert isinstance(registry.value, ast.Tuple), "registry is no longer a tuple"

    in_registry = {el.lineno for el in registry.value.elts}
    orphan_lines: list[int] = []
    for node in tree.body:
        if node is registry or isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and getattr(sub.func, "id", "") == "CommittedFixMarker"
                and sub.lineno not in in_registry
            ):
                orphan_lines.append(sub.lineno)
    assert not orphan_lines, (
        "CommittedFixMarker constructed outside the COMMITTED_FIX_MARKERS "
        f"tuple (orphaned / unguarded entry) at lines {sorted(orphan_lines)}"
    )

    keys = [r.key for r in COMMITTED_FIX_MARKERS]
    assert len(keys) == len(set(keys)), "duplicate fix keys in registry"


@pytest.mark.parametrize(
    "record", COMMITTED_FIX_MARKERS, ids=[r.key for r in COMMITTED_FIX_MARKERS]
)
def test_synthetic_reverted_blob_is_flagged(record: CommittedFixMarker) -> None:
    """Non-vacuity for every registry entry, in its own polarity.

    Positive markers: a fake blob lacking the marker must fail the guard (a
    peer committing a stale staged file reverted the fix).  ``absent`` markers:
    a fake blob that REINTRODUCES the vulnerable pattern must fail instead.
    Neither type may pass vacuously on an unchecked path.
    """
    if record.absent:
        if record.scope is None:
            synthetic = f"# reintroduced vulnerable pattern\n{record.marker}\n"
        else:
            start_anchor, end_anchor = record.scope
            synthetic = f"{start_anchor}\n{record.marker}\n{end_anchor}\n"

        def attacker_blob(path: str) -> str:
            return synthetic

        present, detail = _evaluate(record, attacker_blob)
        assert not present, (
            f"synthetic reintroduction of vulnerable pattern for {record.key!r} "
            "was not caught"
        )
        assert record.key in detail and record.marker in detail
        assert "REINTRODUCED" in detail
        return

    def reverted_blob(path: str) -> str:
        return f"# synthetic reverted blob for {path}\n# mark erased\n"

    present, detail = _evaluate(record, reverted_blob)
    assert not present, f"synthetic committed revert of {record.key!r} was not caught"
    assert record.key in detail and record.marker in detail
    assert "COMMITTED SECURITY FIX MARKER MISSING" in detail


def test_query_cache_committed_keys_are_registered() -> None:
    """The fine-grained classifier keys name real, unique committed entries."""
    keys = [r.key for r in COMMITTED_FIX_MARKERS]
    assert QUERY_CACHE_COMMITTED_KEYS <= set(keys), (
        "query-cache key set references keys missing from COMMITTED_FIX_MARKERS: "
        f"{sorted(QUERY_CACHE_COMMITTED_KEYS - set(keys))}"
    )
    for key in QUERY_CACHE_COMMITTED_KEYS:
        record = next(r for r in COMMITTED_FIX_MARKERS if r.key == key)
        assert record.path == "src/core/database/core/execution.py"


def test_private_index_pinned_keys_are_registered() -> None:
    """Every audited private-index fix is pinned in the committed registry.

    Companion to the registry entries themselves: dropping one of these keys
    (or the whole block) without updating this set fails here as well as in the
    parametrized presence/non-vacuity tests.
    """
    keys = [r.key for r in COMMITTED_FIX_MARKERS]
    assert PRIVATE_INDEX_PINNED_KEYS <= set(keys), (
        "private-index key set references keys missing from COMMITTED_FIX_MARKERS: "
        f"{sorted(PRIVATE_INDEX_PINNED_KEYS - set(keys))}"
    )
    assert len(PRIVATE_INDEX_PINNED_KEYS) == 32
    for key in PRIVATE_INDEX_PINNED_KEYS:
        record = next(r for r in COMMITTED_FIX_MARKERS if r.key == key)
        assert record.path.startswith("src/"), f"bad path for {key!r}"


def test_stale_staged_revert_markers_are_registered_and_non_vacuous(
    commit_sha: str,
) -> None:
    """The stale-staged MM pins are live at HEAD and catch their own revert.

    Each key names a committed security fix whose staged (index) copy in an
    MM file is the pre-fix version.  This proves both sides: the marker is in
    the real committed blob, and a synthetic stale blob with the fix erased is
    rejected by the exact same evaluator (never a vacuous pass).
    """
    keys = [r.key for r in COMMITTED_FIX_MARKERS]
    assert STALE_STAGED_REVERT_KEYS <= set(keys), (
        "stale-staged pin set references keys missing from COMMITTED_FIX_MARKERS: "
        f"{sorted(STALE_STAGED_REVERT_KEYS - set(keys))}"
    )
    assert len(STALE_STAGED_REVERT_KEYS) == 4
    reader = _make_blob_reader(commit_sha)
    for key in sorted(STALE_STAGED_REVERT_KEYS):
        record = next(r for r in COMMITTED_FIX_MARKERS if r.key == key)
        present, detail = _evaluate(record, reader)
        assert present, f"{key} is not pinned at HEAD: {detail}"

        def stale_staged_blob(path: str) -> str:
            return f"# stale staged copy of {path}\n# fix marker erased\n"

        present, detail = _evaluate(record, stale_staged_blob)
        assert not present, f"synthetic stale staged revert of {key} was not caught"
        assert record.key in detail and record.marker in detail
        assert "COMMITTED SECURITY FIX MARKER MISSING" in detail


def test_ws_ticket_binding_pins_are_registered_and_non_vacuous(
    commit_sha: str,
) -> None:
    """Both _verify_ws_ticket copies keep their constant-time binding checks.

    Each pin names a distinctive ip_index/ua_index constant-time compare in one
    of the two ticket-verifier copies. This proves both sides: the compare is in
    the real committed blob of each file, and a synthetic stale blob with that
    compare erased is rejected by the exact same evaluator (never a vacuous
    pass). Per-entry synthetic removal is additionally covered by the
    parametrized ``test_synthetic_reverted_blob_is_flagged``.
    """
    keys = [r.key for r in COMMITTED_FIX_MARKERS]
    assert WS_TICKET_BINDING_KEYS <= set(keys), (
        "WS-ticket binding key set references keys missing from "
        f"COMMITTED_FIX_MARKERS: {sorted(WS_TICKET_BINDING_KEYS - set(keys))}"
    )
    assert len(WS_TICKET_BINDING_KEYS) == 4
    reader = _make_blob_reader(commit_sha)
    by_key = {r.key: r for r in COMMITTED_FIX_MARKERS}
    expected_paths = {
        "3e35a337-ws-ticket-ip-binding-tickets": (
            "src/core/auth/managers/session/tickets.py"
        ),
        "3e35a337-ws-ticket-ua-binding-tickets": (
            "src/core/auth/managers/session/tickets.py"
        ),
        "6b4e443c-ws-ticket-ip-binding-lifecycle-mirror": (
            "src/core/auth/managers/session/lifecycle.py"
        ),
        "6b4e443c-ws-ticket-ua-binding-lifecycle-mirror": (
            "src/core/auth/managers/session/lifecycle.py"
        ),
    }
    for key in sorted(WS_TICKET_BINDING_KEYS):
        record = by_key[key]
        assert record.path == expected_paths[key], (
            f"{key} points at {record.path!r}, expected {expected_paths[key]!r}"
        )
        present, detail = _evaluate(record, reader)
        assert present, f"{key} is not pinned at HEAD: {detail}"

        def stale_ticket_verifier(path: str) -> str:
            return f"# stale staged copy of {path}\n# binding compare erased\n"

        present, detail = _evaluate(record, stale_ticket_verifier)
        assert not present, f"synthetic binding revert of {key} was not caught"
        assert record.key in detail and record.marker in detail
        assert "COMMITTED SECURITY FIX MARKER MISSING" in detail


# Private-index commits identified by the external audit lane as NON-security
# (perf/test-only). Their empty-reflog entries are intentionally not pinned by
# a security marker; documenting them here keeps the reflog enumeration test
# exhaustive rather than silently ignoring them.
PRIVATE_INDEX_NON_SECURITY_SHAS: frozenset[str] = frozenset(
    {"05908b2d", "8e32e6b9", "d9099f91", "e53e9526", "db7a4143"}
)

# Early private-index markers use descriptive keys that do not embed their
# commit SHA. Map those SHA prefixes to their registry key so the reflog
# enumeration below can prove they are still pinned.
PRIVATE_INDEX_KEY_BY_SHA: dict[str, str] = {
    "d39659c3": "n448-channel-detail-cache-key",
    "d6a41435": "n478-thread-messages-limit-clamp",
    "c6a111dd": "n459-server-route-structural-sweep",
}


def test_private_index_pinned_keys_tie_to_real_commits(commit_sha: str) -> None:
    """Non-vacuity: each pinned key's SHA resolves to an ancestor of HEAD.

    This is the git tie that stops PRIVATE_INDEX_PINNED_KEYS from being a mere
    self-referential list: a fabricated key whose prefix is not a real,
    reachable commit fails here. It deliberately does NOT claim the list is
    auto-discovered -- the enumeration itself is the external audit lane's job.
    """
    import re

    for key in sorted(PRIVATE_INDEX_PINNED_KEYS):
        m = re.match(r"^([0-9a-f]{8})-", key)
        assert m, f"pinned key {key!r} lacks an 8-hex commit prefix"
        prefix = m.group(1)
        resolved = _git("rev-parse", "--verify", f"{prefix}^{{commit}}")
        assert resolved.returncode == 0, (
            f"pinned key {key!r} names unknown commit {prefix!r}"
        )
        anc = _git("merge-base", "--is-ancestor", prefix, commit_sha)
        assert anc.returncode == 0, (
            f"pinned key {key!r} names commit {prefix!r} that is not an "
            "ancestor of HEAD"
        )


def test_private_index_reflog_enumeration_has_no_unpinned_security_fix() -> None:
    """Auto-detection: every private-index ancestor is pinned or documented.

    Enumerate empty-reflog entries (the signature of a commit-tree/CAS land)
    via git itself. Every ancestor SHA must either be pinned by a
    PRIVATE_INDEX_PINNED_KEYS entry or be an explicitly documented non-security
    commit. A newly landed private-index fix therefore fails here until it is
    pinned, instead of silently escaping the audit.

    Reflogs are local and may be unavailable (fresh clone / expiry); in that
    case the test skips rather than passing vacuously on an empty set.
    """
    proc = _git("reflog", "--format=%H%x09%gs")
    if proc.returncode != 0:
        pytest.skip("git reflog unavailable; auto-detection not possible")
    empty: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 1 or not parts[1].strip():
            empty.add(parts[0][:8])
    if not empty:
        pytest.skip("no empty-reflog entries available; reflog likely expired")
    registered = {r.key for r in COMMITTED_FIX_MARKERS}
    assert set(PRIVATE_INDEX_KEY_BY_SHA.values()) <= registered, (
        "PRIVATE_INDEX_KEY_BY_SHA references keys missing from the registry"
    )
    # Coverage is over the WHOLE registry: some early private-index fixes
    # (d39659c3, c62071ac, cd9bcf44, ...) are pinned but are deliberately not
    # members of the PRIVATE_INDEX_PINNED_KEYS companion subset.
    all_keys = registered | set(PRIVATE_INDEX_KEY_BY_SHA.values())
    prefixes = {k[:8] for k in all_keys} | set(PRIVATE_INDEX_KEY_BY_SHA)
    unpinned: list[str] = []
    for prefix in sorted(empty):
        anc = _git("merge-base", "--is-ancestor", prefix, "HEAD")
        if anc.returncode != 0:
            continue  # reflog entry no longer reachable from HEAD
        if prefix in prefixes or prefix in PRIVATE_INDEX_NON_SECURITY_SHAS:
            continue
        unpinned.append(prefix)
    assert not unpinned, (
        "private-index commits with no committed-tree marker and no "
        f"non-security exemption: {unpinned}. Pin each with a distinctive "
        "marker or document it in PRIVATE_INDEX_NON_SECURITY_SHAS."
    )


def test_legacy_private_index_shas_keep_their_committed_markers(
    commit_sha: str,
) -> None:
    """The three early private-index repairs stay pinned to real HEAD blobs.

    Those commits predate the SHA-prefixed key convention, so
    PRIVATE_INDEX_KEY_BY_SHA is the only thing tying their empty-reflog entries
    to a registry marker. This makes that tie non-vacuous: each mapping must
    name a registered key whose marker is actually present in the committed
    HEAD blob, and each SHA must be a real ancestor. Dropping or mis-pointing a
    mapping therefore fails here -- not only in the reflog enumeration -- which
    is exactly the "unpinned c6a111dd / d39659c3 / d6a41435" failure mode this
    closes.
    """
    reader = _make_blob_reader(commit_sha)
    by_key = {r.key: r for r in COMMITTED_FIX_MARKERS}
    assert len(PRIVATE_INDEX_KEY_BY_SHA) == 3
    for sha_prefix, key in sorted(PRIVATE_INDEX_KEY_BY_SHA.items()):
        anc = _git("merge-base", "--is-ancestor", sha_prefix, commit_sha)
        assert anc.returncode == 0, f"{sha_prefix} is not an ancestor of HEAD"
        record = by_key.get(key)
        assert record is not None, (
            f"mapping for {sha_prefix} names unknown key {key!r}"
        )
        present, detail = _evaluate(record, reader)
        assert present, f"mapping for {sha_prefix} no longer pins its fix: {detail}"


def test_committed_absent_marker_scopes_resolve(commit_sha: str) -> None:
    """Negative-assertion scopes resolve in the real HEAD blob, non-vacuously."""
    reader = _make_blob_reader(commit_sha)
    for record in COMMITTED_FIX_MARKERS:
        if not (record.absent and record.scope):
            continue
        region = _scoped_text(reader(record.path), record)
        assert region, f"scope {record.scope!r} did not resolve for {record.key!r}"
        assert record.marker not in region, (
            f"vulnerable pattern already committed in the scope of {record.key!r}"
        )


@pytest.mark.parametrize("key", sorted(QUERY_CACHE_COMMITTED_KEYS))
def test_query_cache_committed_marker_non_vacuity(
    key: str, commit_sha: str
) -> None:
    """Each committed sub-fix is live at HEAD and the guard reacts if undone.

    Positive markers are proven by a synthetic reverted blob.  ``absent``
    markers are proven from the other side: a synthetic committed blob that
    REINTRODUCES the vulnerable pattern must be flagged.
    """
    record = next(r for r in COMMITTED_FIX_MARKERS if r.key == key)
    reader = _make_blob_reader(commit_sha)
    present, detail = _evaluate(record, reader)
    assert present, detail

    if record.absent:

        def blob_reintroduces(path: str) -> str:
            if record.scope is None:
                return f"# reintroduced\n{record.marker}\n"
            start_anchor, end_anchor = record.scope
            return f"{start_anchor}\n{record.marker}\n{end_anchor}\n"

        present, detail = _evaluate(record, blob_reintroduces)
        assert not present, (
            f"committed reintroduction of vulnerable pattern for {key!r} "
            "was not detected"
        )
        assert record.key in detail and record.marker in detail
        assert "REINTRODUCED" in detail
    else:

        def reverted_blob(path: str) -> str:
            return f"# synthetic reverted blob for {path}\n# mark erased\n"

        present, detail = _evaluate(record, reverted_blob)
        assert not present, f"synthetic committed revert of {key!r} was not caught"
        assert record.key in detail and record.marker in detail
        assert "COMMITTED SECURITY FIX MARKER MISSING" in detail


def test_undocumented_blob_removal_is_flagged() -> None:
    """A file removed from the commit fails unless a successor is documented."""
    record = CommittedFixMarker(
        key="synthetic-removed-file",
        path="src/tests/security/_does_not_exist_committed_guard.py",
        marker="NEVER_PRESENT",
        why="self-test only",
    )

    def missing_blob(path: str) -> str:
        raise BlobMissing(path, "deleted in commit")

    present, detail = _evaluate(record, missing_blob)
    assert not present
    assert record.key in detail and "missing from commit" in detail


def test_documented_successor_is_tolerated() -> None:
    """A documented relocation is accepted; only documented removal is safe."""
    record = CommittedFixMarker(
        key="synthetic-relocated-file",
        path="old.py",
        marker="MARKER",
        why="self-test only",
        successors=("new.py",),
    )

    def relocated_blob(path: str) -> str:
        if path == "old.py":
            raise BlobMissing(path, "removed")
        return "# relocated fix\nMARKER\n"

    present, detail = _evaluate(record, relocated_blob)
    assert present, detail
    assert "new.py" in detail


def test_reader_returns_committed_blob_not_stale_working_tree(
    tmp_path: Path,
) -> None:
    """Non-vacuity: the reader really reads HEAD, not the working tree.

    A throwaway repo commits a file with the marker, then rewrites the working
    copy to a stale/reverted version.  The same reader used by the guard must
    still see the committed value, proving the check cannot be fooled by a
    stale working-tree/index file.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "fix.py"
    target.write_text("MARKER_PRESENT\n", encoding="utf-8")

    assert _git("init", "-q", cwd=repo).returncode == 0
    assert _git("add", "fix.py", cwd=repo).returncode == 0
    committed = _git(
        "-c",
        "user.email=guard@example.invalid",
        "-c",
        "user.name=guard",
        "commit",
        "-qm",
        "fix",
        cwd=repo,
    )
    assert committed.returncode == 0, committed.stderr
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    assert sha

    # Peer-style stale working copy: the fix is gone from disk/index only.
    target.write_text("MARKER_REMOVED\n", encoding="utf-8")
    assert "MARKER_PRESENT" not in target.read_text(encoding="utf-8")

    reader = _make_blob_reader(sha, repo_root=repo)
    record = CommittedFixMarker(
        key="tmp-committed-vs-working",
        path="fix.py",
        marker="MARKER_PRESENT",
        why="self-test only",
    )
    present, detail = _evaluate(record, reader)
    assert present, detail


def test_oauth_invariant_pins_live_at_head_and_non_vacuous(
    commit_sha: str,
) -> None:
    """The OAuth state/redirect pins are registered, live and falsifiable.

    Each key resolves to a committed blob whose exact text carries the marker;
    a synthetic blob with the fix erased must be flagged by the same evaluator.
    This complements the parametrized synthetic-removal test with a live-HEAD
    tie, so a key that was registered but never matched anything fails here.
    """
    keys = [r.key for r in COMMITTED_FIX_MARKERS]
    assert OAUTH_INVARIANT_PIN_KEYS <= set(keys), (
        "OAuth invariant key set references keys missing from the registry: "
        f"{sorted(OAUTH_INVARIANT_PIN_KEYS - set(keys))}"
    )
    assert len(OAUTH_INVARIANT_PIN_KEYS) == 8
    reader = _make_blob_reader(commit_sha)
    for key in sorted(OAUTH_INVARIANT_PIN_KEYS):
        record = next(r for r in COMMITTED_FIX_MARKERS if r.key == key)
        present, detail = _evaluate(record, reader)
        assert present, f"{key} is not pinned at HEAD: {detail}"

        def reverted_blob(path: str) -> str:
            return f"# stale committed copy of {path}\n# OAuth fix erased\n"

        present, detail = _evaluate(record, reverted_blob)
        assert not present, f"synthetic committed revert of {key} was not caught"
        assert record.key in detail and record.marker in detail
        assert "COMMITTED SECURITY FIX MARKER MISSING" in detail


def test_alias_automod_pins_live_at_head_and_non_vacuous(
    commit_sha: str,
) -> None:
    """Both N690 alias-automod pins are registered, live and falsifiable.

    Each key resolves to a real committed blob carrying its distinctive
    _enforce_alias_automod definition/call, points at the expected file, and is
    rejected by a synthetic stale blob with the fix erased. Per-entry
    parametrized synthetic-removal coverage is already provided by
    ``test_synthetic_reverted_blob_is_flagged``.
    """
    keys = [r.key for r in COMMITTED_FIX_MARKERS]
    assert ALIAS_AUTOMOD_PIN_KEYS <= set(keys), (
        "alias-automod pin set references keys missing from the registry: "
        f"{sorted(ALIAS_AUTOMOD_PIN_KEYS - set(keys))}"
    )
    assert len(ALIAS_AUTOMOD_PIN_KEYS) == 2
    reader = _make_blob_reader(commit_sha)
    by_key = {r.key: r for r in COMMITTED_FIX_MARKERS}
    expected_paths = {
        "627accb2-alias-automod-enforcement": (
            "src/api/routes/messages/messages_crud/base.py"
        ),
        "627accb2-alias-automod-send-call": (
            "src/api/routes/messages/messages_crud/send.py"
        ),
    }
    for key in sorted(ALIAS_AUTOMOD_PIN_KEYS):
        record = by_key[key]
        assert record.path == expected_paths[key], (
            f"{key} points at {record.path!r}, expected {expected_paths[key]!r}"
        )
        present, detail = _evaluate(record, reader)
        assert present, f"{key} is not pinned at HEAD: {detail}"

        def stale_alias_automod_blob(path: str) -> str:
            return f"# stale staged copy of {path}\n# alias automod gate erased\n"

        present, detail = _evaluate(record, stale_alias_automod_blob)
        assert not present, f"synthetic stale alias-automod revert of {key} was not caught"
        assert record.key in detail and record.marker in detail
        assert "COMMITTED SECURITY FIX MARKER MISSING" in detail

# cdeda33b N700/N642: companion key set for the two assign_role re-check pins,
# kept explicit so neither can be dropped from the registry silently.
ROLE_ASSIGNMENT_TOCTOU_PIN_KEYS: frozenset[str] = frozenset(
    {
        "cdeda33b-role-assignment-grantability-recheck",
        "cdeda33b-role-assignment-rank-recheck",
    }
)


def test_role_assignment_toctou_pins_live_at_head_and_non_vacuous(
    commit_sha: str,
) -> None:
    """Both under-lock assign_role re-checks are pinned at HEAD and falsifiable.

    Each key resolves to a real committed blob whose exact text carries the
    re-read + fail-closed raise + re-decision, points at assignments.py, and is
    rejected by a synthetic stale blob with the fix erased. Per-entry parametrized
    synthetic-removal coverage is already provided by
    ``test_synthetic_reverted_blob_is_flagged``.
    """
    keys = [r.key for r in COMMITTED_FIX_MARKERS]
    assert ROLE_ASSIGNMENT_TOCTOU_PIN_KEYS <= set(keys), (
        "assign_role pin set references keys missing from the registry: "
        f"{sorted(ROLE_ASSIGNMENT_TOCTOU_PIN_KEYS - set(keys))}"
    )
    assert len(ROLE_ASSIGNMENT_TOCTOU_PIN_KEYS) == 2
    reader = _make_blob_reader(commit_sha)
    by_key = {r.key: r for r in COMMITTED_FIX_MARKERS}
    for key in sorted(ROLE_ASSIGNMENT_TOCTOU_PIN_KEYS):
        record = by_key[key]
        assert record.path == "src/core/servers/handlers/roles/assignments.py"
        present, detail = _evaluate(record, reader)
        assert present, f"{key} is not pinned at HEAD: {detail}"

        def stale_assignments_blob(path: str) -> str:
            return f"# stale copy of {path}\n# under-lock re-check erased\n"

        present, detail = _evaluate(record, stale_assignments_blob)
        assert not present, f"synthetic assign_role revert of {key} not caught"
        assert record.key in detail and record.marker in detail
        assert "COMMITTED SECURITY FIX MARKER MISSING" in detail
