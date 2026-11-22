from typing import Optional, List, Dict, Set
import json
import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.servers.permission_utils import (
    calculate_base_permissions,
    apply_channel_overrides,
    has_permission as check_permission,
)


from .bounds import blocked_users_not_in
from .protocol import ReactionProtocol


# Cap every ``IN (...)`` fan-out so a large audience cannot exceed the backend
# bind-variable cap. One chunk costs a fixed number of set-based queries no
# matter how many members the server has.
_BULK_READ_AUDIENCE_CHUNK = 500


def _is_int(value) -> bool:
    """True only for ids that can be bound as an integer (never a bool)."""
    if isinstance(value, bool):
        return False
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def _uses_canonical_has_permission(servers) -> bool:
    """Whether ``servers.has_permission`` is the stock manager implementation.

    A caller may replace it with extra authorization policy (a stub in tests,
    a deployment wrapper in production). The batched resolver mirrors only the
    stock tables/helpers, so bypassing an override could widen a deliberately
    narrowed audience. When it is not canonical we keep the per-member gate.
    """
    try:
        from src.core.servers.manager.permission_ops import PermissionOpsMixin

        canonical = PermissionOpsMixin.has_permission
    except Exception:
        return False
    method = getattr(servers, "has_permission", None)
    return getattr(method, "__func__", None) is canonical


def _bulk_channel_read_user_ids(db, user_ids, server_id, channel_id):
    """Resolve ``messages.read`` for an audience of server members at once.

    The reaction fan-out (and the read-receipt/ACK audience scoping sharing
    ``get_participant_ids``) used to ask ``has_permission`` once per member --
    one DB/cache round trip per roster row on a cold permission snapshot.

    Mirrors the per-user resolver exactly: same owner/member gates, same
    ``calculate_base_permissions`` / ``apply_channel_overrides`` helpers, same
    lowest-position-first role-override ordering and same ``check_permission``
    verdict. Only the batching differs. Ids are strictly coerced to int and
    de-duplicated; the result is keyed by the ids actually passed in, so no
    caller can pick up another user's verdict. Any lookup error fails closed:
    the affected user is omitted, never granted on an unproven row.
    """
    allowed = set()
    ids = []
    for raw in user_ids:
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            continue
        ids.append(uid)
    ids = list(dict.fromkeys(ids))
    if not ids:
        return allowed

    try:
        server_row = db.fetch_one(
            "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
            (server_id,),
        )
        if not server_row:
            return allowed
        owner_id = int(server_row["owner_id"])
    except Exception:
        return allowed

    for start in range(0, len(ids), _BULK_READ_AUDIENCE_CHUNK):
        chunk = ids[start : start + _BULK_READ_AUDIENCE_CHUNK]
        try:
            ph = ",".join("?" for _ in chunk)
            member_rows = (
                db.fetch_all(
                    "SELECT id, user_id FROM srv_members "
                    f"WHERE server_id = ? AND user_id IN ({ph})",
                    (server_id, *chunk),
                )
                or []
            )
            members = {}
            member_user_by_id = {}
            for row in member_rows:
                try:
                    uid = int(row["user_id"])
                    mid = int(row["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                members[uid] = row
                member_user_by_id[mid] = uid

            roles_by_user = {uid: [] for uid in chunk}
            member_ids = list(member_user_by_id)
            if member_ids:
                mph = ",".join("?" for _ in member_ids)
                role_rows = (
                    db.fetch_all(
                        "SELECT r.*, mr.member_id FROM srv_roles r "
                        "INNER JOIN srv_member_roles mr ON r.id = mr.role_id "
                        f"WHERE mr.member_id IN ({mph}) AND r.deleted = 0",
                        tuple(member_ids),
                    )
                    or []
                )
                for row in role_rows:
                    try:
                        uid = member_user_by_id.get(int(row["member_id"]))
                    except (KeyError, TypeError, ValueError):
                        uid = None
                    if uid is not None:
                        roles_by_user[uid].append(dict(row))

            overrides_by_role = {}
            all_role_ids = list(
                dict.fromkeys(
                    int(r["id"])
                    for rows in roles_by_user.values()
                    for r in rows
                    if "id" in r
                )
            )
            for rstart in range(0, len(all_role_ids), _BULK_READ_AUDIENCE_CHUNK):
                role_chunk = all_role_ids[rstart : rstart + _BULK_READ_AUDIENCE_CHUNK]
                rph = ",".join("?" for _ in role_chunk)
                rows = (
                    db.fetch_all(
                        "SELECT * FROM srv_channel_overrides "
                        f"WHERE channel_id = ? AND target_type = 'role' "
                        f"AND target_id IN ({rph})",
                        (channel_id, *role_chunk),
                    )
                    or []
                )
                for row in rows:
                    try:
                        overrides_by_role.setdefault(int(row["target_id"]), []).append(
                            dict(row)
                        )
                    except (KeyError, TypeError, ValueError):
                        continue

            member_overrides = {}
            rows = (
                db.fetch_all(
                    "SELECT * FROM srv_channel_overrides "
                    f"WHERE channel_id = ? AND target_type = 'member' "
                    f"AND target_id IN ({ph})",
                    (channel_id, *chunk),
                )
                or []
            )
            for row in rows:
                try:
                    member_overrides[int(row["target_id"])] = dict(row)
                except (KeyError, TypeError, ValueError):
                    continue

            for uid in chunk:
                try:
                    is_owner = uid == owner_id
                    if uid not in members and not is_owner:
                        continue
                    role_rows = roles_by_user.get(uid, [])
                    base = calculate_base_permissions(role_rows, is_owner)
                    positions = {
                        int(r["id"]): int(r.get("position") or 0) for r in role_rows
                    }
                    role_overrides = []
                    for role_row in role_rows:
                        role_overrides.extend(
                            overrides_by_role.get(int(role_row["id"]), [])
                        )
                    role_overrides.sort(
                        key=lambda o: positions.get(int(o.get("target_id") or 0), 0)
                    )
                    perms = apply_channel_overrides(
                        base, role_overrides, member_overrides.get(uid)
                    )
                    if check_permission(perms, "messages.read"):
                        allowed.add(uid)
                except Exception:
                    continue
        except Exception as exc:
            logger.debug(f"bulk reaction audience resolve failed: {exc}")
            continue
    return allowed


class ReactionPermissionsMixin(ReactionProtocol):
    def get_conversation_id_from_message(
        self, message_id: SnowflakeID
    ) -> Optional[SnowflakeID]:
        row = self._db.fetch_one(
            "SELECT conversation_id FROM msg_messages WHERE id = ?", (message_id,)
        )
        return row["conversation_id"] if row else None

    def resolve_message_channel(
        self, message_id: SnowflakeID
    ) -> Optional[Dict[str, Optional[SnowflakeID]]]:
        """Resolve the conversation and real channel id for a message.

        Returns ``None`` when the message does not exist. ``channel_id`` is
        the srv_channels id for server channels, otherwise the conversation
        id itself (DM/group conversations have no channel row).
        """
        # One query instead of a message-row fetch followed by a separate
        # channel lookup. The reactor-list route already paid for a message
        # fetch in its access gate (and the core manager pays for another one),
        # so resolving the URL channel must not add a second trip to the same
        # table. LEFT JOIN keeps DMs (no channel row) and missing messages
        # behaving exactly as before; there is no ``deleted`` filter on either
        # side because the previous two-query form had none either.
        row = self._db.fetch_one(
            """SELECT m.conversation_id, sc.id AS channel_id
               FROM msg_messages m
               LEFT JOIN srv_channels sc ON sc.conversation_id = m.conversation_id
               WHERE m.id = ?""",
            (message_id,),
        )
        if not row or not row["conversation_id"]:
            return None
        conversation_id = row["conversation_id"]
        channel_id = row["channel_id"]
        return {
            "conversation_id": conversation_id,
            "channel_id": channel_id or conversation_id,
            "is_server_channel": channel_id is not None,
        }

    def can_access_message(self, user_id: SnowflakeID, message_id: SnowflakeID) -> bool:
        """Cheap, live authorization gate for reaction reads.

        Fetches only the message row and re-checks the viewer's participation
        through the same ``_is_participant`` check the cold read path uses.
        Unlike :meth:`get_reactions` it does not aggregate or serialize any
        reactions, so callers can run it on *every* request -- ahead of any
        response-cache lookup -- without paying for the expensive part of a
        reaction read. A missing message or a viewer without access returns
        ``False`` (existence is concealed).
        """
        msg = self._get_message(message_id)
        if not msg:
            return False
        return self._is_participant(msg["conversation_id"], user_id)

    def get_participant_ids(self, conversation_id: SnowflakeID) -> List[SnowflakeID]:
        participant_rows = self._db.fetch_all(
            "SELECT user_id FROM msg_participants WHERE conversation_id = ?",
            (conversation_id,),
        )
        participant_ids = [row["user_id"] for row in participant_rows]

        conv_row = self._db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?", (conversation_id,)
        )
        # L7: mirror _is_participant's explicit key-presence guard so a row
        # whose metadata column is NULL/missing is treated identically there
        # and here, instead of relying on dict.get() alone.
        if conv_row and "metadata" in conv_row.keys() and conv_row["metadata"]:
            try:
                metadata = (
                    json.loads(conv_row["metadata"])
                    if isinstance(conv_row["metadata"], str)
                    else conv_row["metadata"]
                )
                server_id = (
                    metadata.get("server_id") if isinstance(metadata, dict) else None
                )
                if server_id:
                    channel = self._get_channel_for_conversation(conversation_id)
                    channel_id = channel["id"] if channel else None
                    member_rows = self._db.fetch_all(
                        "SELECT user_id FROM srv_members WHERE server_id = ?",
                        (server_id,),
                    )
                    # SECURITY (Agent 21 F2): fail CLOSED when the servers
                    # module is not wired. Without it we cannot evaluate
                    # ``messages.read`` for this channel, so unverified
                    # server members must NOT receive the reaction event.
                    # This mirrors ``_check_server_permission``, which also
                    # denies (rather than allows) actions it cannot verify.
                    if not self._servers:
                        logger.warning(
                            "Servers module unavailable; not fanning reaction "
                            f"events out to unverified members of server {server_id}"
                        )
                    else:
                        # Only fan out to members who can actually read the
                        # channel; large servers otherwise broadcast every
                        # reaction to thousands of invisible recipients.
                        # Resolve the whole roster with bounded IN queries
                        # instead of one permission lookup per member. The
                        # verdict is keyed strictly by the candidate ids and
                        # any lookup failure excludes the affected user.
                        existing = {int(pid) for pid in participant_ids if _is_int(pid)}
                        candidates = []
                        for row in member_rows:
                            member_id = row["user_id"]
                            if not _is_int(member_id):
                                continue
                            key = int(member_id)
                            if key in existing:
                                continue
                            candidates.append(key)
                        if candidates and _uses_canonical_has_permission(self._servers):
                            allowed = _bulk_channel_read_user_ids(
                                self._db, candidates, server_id, channel_id
                            )
                            added = set(existing)
                            for row in member_rows:
                                if not _is_int(row["user_id"]):
                                    continue
                                mid = int(row["user_id"])
                                if mid in allowed and mid not in added:
                                    participant_ids.append(mid)
                                    added.add(mid)
                        else:
                            # A replaced has_permission may carry policy the
                            # batch resolver cannot see; keep the per-member gate.
                            for row in member_rows:
                                member_id = row["user_id"]
                                if not _is_int(member_id):
                                    continue
                                key = int(member_id)
                                if key in existing:
                                    continue
                                try:
                                    if not self._servers.has_permission(
                                        member_id,
                                        server_id,
                                        "messages.read",
                                        channel_id,
                                    ):
                                        continue
                                except Exception as e:
                                    logger.debug(
                                        f"reaction audience permission check failed "
                                        f"for {member_id}: {e}"
                                    )
                                    continue
                                participant_ids.append(member_id)
                                existing.add(key)
            except (json.JSONDecodeError, TypeError, AttributeError) as e:
                logger.debug(
                    f"Failed to parse conversation metadata for reactions: {e}"
                )

        return participant_ids

    def _is_participant(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        row = self._db.fetch_one(
            "SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        if row:
            return True

        conv_row = self._db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?", (conversation_id,)
        )
        if conv_row:
            metadata_str = (
                conv_row["metadata"] if "metadata" in conv_row.keys() else None
            )
            if metadata_str:
                try:
                    metadata = (
                        json.loads(metadata_str)
                        if isinstance(metadata_str, str)
                        else metadata_str
                    )
                    server_id = (
                        metadata.get("server_id")
                        if isinstance(metadata, dict)
                        else None
                    )
                    if server_id:
                        member_row = self._db.fetch_one(
                            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
                            (server_id, user_id),
                        )
                        return member_row is not None
                except (json.JSONDecodeError, TypeError):
                    pass

        return False

    def _get_channel_for_conversation(
        self, conversation_id: SnowflakeID
    ) -> Optional[Dict]:
        """Resolve the backing server channel for either accepted id form.

        Besides the linked ``conversation_id``, callers can address a server
        channel by its own ``id``: ``messages_crud`` stores
        ``msg_messages.conversation_id = srv_channels.id`` when the conversation
        link is missing and cannot be repaired, and ``is_participant`` grants
        server members participation in that id via ``srv_channels.id``
        (``WHERE conversation_id = ? OR id = ?``) regardless of the per-channel
        grant. A lookup keyed only on ``conversation_id`` misses that alias,
        makes the channel look like a genuine DM, and makes every caller skip
        its live ``messages.add_reactions`` / ``messages.manage`` / read-audience
        gate. Resolve both id forms so the same gate is applied either way.
        """
        return self._db.fetch_one(
            "SELECT * FROM srv_channels WHERE conversation_id = ? OR id = ?",
            (conversation_id, conversation_id),
        )

    def _check_server_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
        permission: str = "messages.add_reactions",
    ) -> bool:
        # Fail CLOSED: if the servers module is not wired we cannot verify
        # the user's permissions, so server-channel actions are denied.
        if not self._servers:
            logger.warning(
                f"Servers module unavailable; denying server permission "
                f"'{permission}' for user {user_id} in server {server_id}"
            )
            return False
        return self._servers.has_permission(user_id, server_id, permission, channel_id)

    def _is_blocked_by_either(
        self, user_id: SnowflakeID, other_id: SnowflakeID
    ) -> bool:
        if not self._relationships:
            return False
        return self._relationships.is_blocked_by_either(user_id, other_id)

    def _get_blocked_user_ids(self, viewer_id: Optional[SnowflakeID]) -> Set[int]:
        """Collect every user id whose reactions ``viewer_id`` must not see."""
        blocked: Set[int] = set()
        if not viewer_id or not self._relationships:
            return blocked
        blocked.update(self._relationships.get_blocked_user_ids(viewer_id))
        rows = self._db.fetch_all(
            "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?", (viewer_id,)
        )
        for row in rows:
            blocked.add(row["blocker_id"])
        return blocked

    def _blocked_users_clause(
        self, column: str, viewer_id: Optional[SnowflakeID]
    ) -> tuple[str, list]:
        """Bounded SQL form of ``_get_blocked_user_ids`` for read queries.

        Both directions of ``rel_blocked`` are resolved in-database so the
        number of bind variables never grows with the size of the block list.
        When no relationships module is wired the legacy behaviour (no
        filtering) is preserved by failing open here, matching
        ``_get_blocked_user_ids``.
        """
        if not viewer_id or not self._relationships:
            return "", []
        return blocked_users_not_in(column, viewer_id)
