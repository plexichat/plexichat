"""Stale-state probes: template creator checks must be re-verified by the
mutating statement.

``delete_template``/``update_template`` load a template, check
``creator_id == user_id``, then delete/update by id only.  A concurrent
creator reassignment in that window would let the former creator delete or
edit a template that now belongs to someone else.  The mutating SQL must
carry the creator predicate and fail closed on a lost race.
"""

import pytest

from src.core.servers.exceptions import PermissionDeniedError


def _wrap_execute(manager, trigger_prefix, inject):
    real_execute = manager._db.execute

    def wrapped(query, params=None, *args, **kwargs):
        if isinstance(query, str) and query.lstrip().upper().startswith(trigger_prefix):
            inject(real_execute)
        return real_execute(query, params, *args, **kwargs)

    manager._db.execute = wrapped
    return real_execute


def _make_template(server_manager, owner, name):
    server = server_manager.create_server(owner.id, "Template race server")
    return server_manager.create_template(owner.id, server.id, name)


def test_delete_template_denied_when_creator_changes_before_write(
    server_manager, two_users
):
    owner, other = two_users
    template = _make_template(server_manager, owner, "TOCTOU delete template")

    def inject(real_execute):
        real_execute(
            "UPDATE srv_templates SET creator_id = ? WHERE id = ?",
            (other.id, template.id),
        )

    real_execute = _wrap_execute(
        server_manager, "DELETE FROM SRV_TEMPLATE_DATA", inject
    )
    try:
        with pytest.raises(PermissionDeniedError):
            server_manager.delete_template(owner.id, template.code)
    finally:
        server_manager._db.execute = real_execute

    row = server_manager._db.fetch_one(
        "SELECT creator_id FROM srv_templates WHERE id = ?", (template.id,)
    )
    assert row is not None
    assert int(row["creator_id"]) == int(other.id)
    data = server_manager._db.fetch_one(
        "SELECT template_id FROM srv_template_data WHERE template_id = ?",
        (template.id,),
    )
    assert data is not None


def test_update_template_denied_when_creator_changes_before_write(
    server_manager, two_users
):
    owner, other = two_users
    template = _make_template(server_manager, owner, "TOCTOU update template")

    def inject(real_execute):
        real_execute(
            "UPDATE srv_templates SET creator_id = ? WHERE id = ?",
            (other.id, template.id),
        )

    real_execute = _wrap_execute(server_manager, "UPDATE SRV_TEMPLATES SET", inject)
    try:
        with pytest.raises(PermissionDeniedError):
            server_manager.update_template(owner.id, template.code, name="Hijacked")
    finally:
        server_manager._db.execute = real_execute

    row = server_manager._db.fetch_one(
        "SELECT name, creator_id FROM srv_templates WHERE id = ?", (template.id,)
    )
    assert row["name"] == "TOCTOU update template"
    assert int(row["creator_id"]) == int(other.id)


def test_creator_can_still_delete_template_when_unchanged(server_manager, two_users):
    """Guard against over-hardening: the legitimate creator delete works."""
    owner, _other = two_users
    template = _make_template(server_manager, owner, "Normal delete template")

    assert server_manager.delete_template(owner.id, template.code) is True
    assert (
        server_manager._db.fetch_one(
            "SELECT id FROM srv_templates WHERE id = ?", (template.id,)
        )
        is None
    )


def test_creator_can_still_update_template_when_unchanged(server_manager, two_users):
    """Guard against over-hardening: the legitimate creator update works."""
    owner, _other = two_users
    template = _make_template(server_manager, owner, "Normal update template")

    updated = server_manager.update_template(owner.id, template.code, name="Renamed")
    assert updated.name == "Renamed"
