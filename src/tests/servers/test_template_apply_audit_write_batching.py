"""Applying a template batches its per-item audit writes.

``apply_template`` emits one ``srv_audit_log`` INSERT per created channel and
role (plus the server-create row).  A template with the configured ceiling of
100 channels / 50 roles therefore issued 151 separate INSERT+autocommit pairs
inside one synchronous request.  The audit buffer used by the other bulk audit
producers (bulk role assignment, temporary-membership purge) must be used here
too, so those rows collapse into a single bounded ``execute_many`` flush.

This test guards both sides of the change: the query-shape reduction and the
fact that no accepted audit row is dropped.
"""

import json
import time
import uuid
from contextlib import nullcontext
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.servers


def _seed_template(db, owner_id, n_channels, n_roles):
    """Insert a template whose snapshot has ``n_channels``/``n_roles`` items.

    Seeding the snapshot directly avoids paying the audit-write cost of
    creating every channel/role through the API just to build the template.
    """
    from src.utils.encryption import generate_snowflake_id

    now = int(time.time() * 1000)
    template_id = generate_snowflake_id()
    code = f"ab{uuid.uuid4().hex[:10]}"
    db.execute(
        "INSERT INTO srv_templates "
        "(id, name, description, creator_id, source_server_id, code, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (template_id, "Batching Template", None, owner_id, None, code, now, now),
    )
    channels = [
        {
            "name": f"batch-ch-{i}",
            "channel_type": "text",
            "category_position": None,
            "position": i,
            "topic": None,
            "nsfw": False,
            "slowmode_seconds": 0,
        }
        for i in range(n_channels)
    ]
    roles = [
        {
            "name": f"batch-role-{i}",
            "permissions": {},
            "color": None,
            "hoist": False,
            "mentionable": False,
            "position": i,
        }
        for i in range(n_roles)
    ]
    db.execute(
        "INSERT INTO srv_template_data "
        "(id, template_id, channels, categories, roles, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            generate_snowflake_id(),
            template_id,
            json.dumps(channels),
            json.dumps([]),
            json.dumps(roles),
            now,
        ),
    )
    return code


class _AuditWriteCounter:
    """Count audit INSERT statements, distinguishing scalar from batched."""

    def __init__(self, db):
        self.db = db
        self.scalar = 0
        self.batched = 0
        self.batched_rows = 0

    def __enter__(self):
        self._orig_execute = self.db.execute
        self._orig_many = self.db.execute_many
        self.db.execute = self._execute
        self.db.execute_many = self._execute_many
        return self

    def __exit__(self, *exc):
        self.db.execute = self._orig_execute
        self.db.execute_many = self._orig_many
        return False

    def _execute(self, sql, params=None, **kwargs):
        if "INSERT INTO srv_audit_log" in str(sql):
            self.scalar += 1
        return self._orig_execute(sql, params, **kwargs)

    def _execute_many(self, sql, rows, **kwargs):
        if "INSERT INTO srv_audit_log" in str(sql):
            self.batched += 1
            self.batched_rows += len(rows)
        return self._orig_many(sql, rows, **kwargs)


@pytest.mark.servers
class TestTemplateApplyAuditBatching:
    def test_apply_template_batches_audit_writes(self, server_with_members, db):
        (
            server,
            owner,
            admin_user,
            member_user,
            outsider,
            admin_role,
            servers,
        ) = server_with_members
        n_channels, n_roles = 12, 8
        expected_rows = 1 + n_channels + n_roles  # server + channels + roles
        code = _seed_template(db, owner.id, n_channels, n_roles)

        # "Before": disable the buffer to observe the historical per-event
        # behaviour. One scalar INSERT per emitted audit row.
        with patch.object(
            servers, "_buffered_audit", lambda capacity=256: nullcontext()
        ):
            with _AuditWriteCounter(db) as before:
                servers.apply_template(
                    user_id=owner.id,
                    code=code,
                    server_name=f"before-{uuid.uuid4().hex[:8]}",
                )
        assert before.scalar == expected_rows, (
            f"unbuffered apply_template should issue {expected_rows} audit "
            f"INSERTs, got {before.scalar}"
        )
        assert before.batched == 0

        # "After": the real buffered path must collapse those INSERTs into one
        # execute_many while writing exactly the same number of rows.
        with _AuditWriteCounter(db) as after:
            new_server = servers.apply_template(
                user_id=owner.id,
                code=code,
                server_name=f"after-{uuid.uuid4().hex[:8]}",
            )
        assert after.scalar == 0, (
            f"buffered apply_template should issue no scalar audit INSERTs, "
            f"got {after.scalar}"
        )
        assert after.batched == 1, (
            f"expected one batched audit flush, got {after.batched}"
        )
        assert after.batched_rows == expected_rows
        assert after.scalar + after.batched < before.scalar + before.batched

        # Completeness: every audit row for this operation reached the table.
        rows = db.fetch_all(
            "SELECT action FROM srv_audit_log WHERE server_id = ?",
            (new_server.id,),
        )
        assert len(rows) == expected_rows
