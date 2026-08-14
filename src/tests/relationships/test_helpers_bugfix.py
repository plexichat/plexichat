"""
Regression test for Bug R3: ``_relationship_to_response`` previously read a
non-existent ``target_id`` attribute (the model field is ``target_user_id``)
and fell back to returning the relationship-row *owner* ``user_id`` instead of
the counterparty. The function now resolves the counterparty via
``target_user_id`` (falling back to ``user_id``).
"""

from src.api.routes.relationships.helpers import _relationship_to_response


class _FakeRelationship:
    def __init__(self, user_id, target_user_id, status):
        self.user_id = user_id
        self.target_user_id = target_user_id
        self.status = status
        self.created_at = 123


def test_counterparty_user_id_resolved_from_target_user_id():
    rel = _FakeRelationship(user_id=1, target_user_id=999, status="friend")
    out = _relationship_to_response(rel)
    assert int(out.user_id) == 999


def test_fallback_to_owner_user_id_when_no_target():
    rel = _FakeRelationship(user_id=7, target_user_id=0, status="blocked")
    out = _relationship_to_response(rel)
    assert int(out.user_id) == 7


def test_status_passthrough():
    rel = _FakeRelationship(user_id=1, target_user_id=2, status="pending_incoming")
    out = _relationship_to_response(rel)
    assert out.status == "pending_incoming"
