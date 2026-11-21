import json
from typing import List, Optional

from src.core.base import SnowflakeID
from ..models import Rule, Exemption
from ..exceptions import ExemptionError


from .protocol import AutoModProtocol


class ExemptionMixin(AutoModProtocol):
    def _get_user_role_ids(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> List[SnowflakeID]:
        """Return the role ids ``user_id`` holds in ``server_id``.

        This is the single read of the (server, user) role set. ``check_message``
        calls it once per message and threads the result through every exemption
        check. The automod evaluation hot path used to re-run this identical
        ``JOIN`` once per rule (an N+1 read): for M rules that was M+1 identical
        round trips carrying exactly the same rows.
        """
        user_roles = self._db.fetch_all(
            """SELECT role_id FROM srv_member_roles 
               JOIN srv_members ON srv_member_roles.member_id = srv_members.id
               WHERE srv_members.server_id = ? AND srv_members.user_id = ?""",
            (server_id, user_id),
        )
        return [r["role_id"] for r in user_roles]

    def _is_exempt(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        user_role_ids: Optional[List[SnowflakeID]] = None,
    ) -> bool:
        server_row = self._db.fetch_one(
            "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
            (server_id,),
        )
        # SECURITY: a projected srv_servers.owner_id vouches for the caller's
        # ownership (and therefore an exemption from every automod rule) only
        # while it is a genuine integer id. Coercing a malformed-but-coercible
        # value with int() ("05"->5, 5.9->5, True->1) would mark a non-owner as
        # the server owner and silently exempt them from automod. Fail closed:
        # only an exact integer id from the authoritative owner row may satisfy
        # the check (type() also rejects bool).
        if server_row:
            owner_id = server_row["owner_id"]
            if type(owner_id) is int and owner_id == int(user_id):
                return True

        channel_exempt = self._db.fetch_one(
            """SELECT id FROM automod_exemptions 
               WHERE server_id = ? AND target_type = 'channel' AND target_id = ? AND rule_id IS NULL""",
            (server_id, channel_id),
        )
        if channel_exempt:
            return True

        # Reuse the role ids resolved once for this check when supplied. When
        # called directly (e.g. check_user) fall back to a fresh read so the
        # legacy call signature keeps its exact behaviour.
        role_ids = (
            user_role_ids
            if user_role_ids is not None
            else self._get_user_role_ids(server_id, user_id)
        )

        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            role_rows = self._db.fetch_all(
                f"SELECT permissions FROM srv_roles WHERE id IN ({placeholders}) AND deleted = 0",
                tuple(role_ids),
            )
            for rr in role_rows:
                try:
                    perms = json.loads(rr.get("permissions") or "{}")
                except Exception:
                    perms = {}
                if isinstance(perms, dict) and perms.get("administrator") is True:
                    return True

        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            role_exempt = self._db.fetch_one(
                f"""SELECT id FROM automod_exemptions
                    WHERE server_id = ? AND target_type = 'role' AND target_id IN ({placeholders}) AND rule_id IS NULL""",
                (server_id, *role_ids),
            )
            if role_exempt:
                return True

        return False

    def _is_exempt_from_rule(
        self,
        rule: Rule,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        user_role_ids: Optional[List[SnowflakeID]] = None,
    ) -> bool:
        if channel_id in rule.exempt_channels:
            return True

        # ``check_message`` resolves the role set once for the whole message
        # and passes it to every rule. Only direct callers omit it, in which
        # case a fresh read preserves the legacy behaviour.
        role_ids = (
            user_role_ids
            if user_role_ids is not None
            else self._get_user_role_ids(rule.server_id, user_id)
        )
        held_roles = set(role_ids)

        if held_roles & set(rule.exempt_roles):
            return True

        if rule.applied_roles:
            if not (held_roles & set(rule.applied_roles)):
                return True

        channel_exempt = self._db.fetch_one(
            """SELECT id FROM automod_exemptions 
               WHERE server_id = ? AND target_type = 'channel' AND target_id = ? AND rule_id = ?""",
            (rule.server_id, channel_id, rule.id),
        )
        if channel_exempt:
            return True

        if held_roles:
            placeholders = ",".join("?" * len(held_roles))
            role_exempt = self._db.fetch_one(
                f"""SELECT id FROM automod_exemptions
                    WHERE server_id = ? AND target_type = 'role' AND target_id IN ({placeholders}) AND rule_id = ?""",
                (rule.server_id, *held_roles, rule.id),
            )
            if role_exempt:
                return True

        return False

    def add_exemption(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
        rule_id: Optional[SnowflakeID] = None,
    ) -> Exemption:
        if target_type not in ["role", "channel"]:
            raise ExemptionError("target_type must be 'role' or 'channel'")

        self._authorize_automod_mutation(server_id, user_id)

        existing = self._db.fetch_one(
            """SELECT id FROM automod_exemptions 
               WHERE server_id = ? AND target_type = ? AND target_id = ? AND rule_id IS ?""",
            (server_id, target_type, target_id, rule_id),
        )

        if existing:
            raise ExemptionError("Exemption already exists")

        now = self._get_timestamp()
        exemption_id = self._generate_id()

        self._db.execute(
            """INSERT INTO automod_exemptions 
               (id, server_id, rule_id, target_type, target_id, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (exemption_id, server_id, rule_id, target_type, target_id, now, user_id),
        )

        return Exemption(
            id=exemption_id,
            server_id=server_id,
            rule_id=rule_id,
            target_type=target_type,
            target_id=target_id,
            created_at=now,
            created_by=user_id,
        )

    def remove_exemption(self, user_id: SnowflakeID, exemption_id: SnowflakeID) -> bool:
        existing = self._db.fetch_one(
            "SELECT id, server_id FROM automod_exemptions WHERE id = ?",
            (exemption_id,),
        )

        if not existing:
            raise ExemptionError("Exemption not found")

        # Server-scope the removal so a caller cannot delete exemptions from
        # servers they do not hold server.automod permission in.
        self._authorize_automod_mutation(existing["server_id"], user_id)

        self._db.execute("DELETE FROM automod_exemptions WHERE id = ?", (exemption_id,))
        return True
