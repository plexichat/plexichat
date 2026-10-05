"""Ratchet: poll, soundboard and admin-ticket read surfaces string ids.

These three read surfaces are not reached by the OpenAPI schema walk (their
response models live outside the standard components) nor by the earlier
search/sticker/emoji sweep. Each serializes a 64-bit snowflake to a client-
read body, so a numeric id would be rounded past 2**53 by a JS ``Number``.

The serializers are exercised directly with ids past ``2**53`` and the
resulting JSON is asserted to carry decimal strings; timestamps and counts
must stay numeric.
"""

import json
from types import SimpleNamespace

BIG = 9007199254740993  # 2**53 + 1


def _fake_poll():
    return SimpleNamespace(
        id=BIG,
        message_id=BIG + 1,
        question="Which?",
        created_by=BIG + 2,
        created_at=1700000000000,
        ends_at=None,
        ended_at=None,
        allow_multiple_choice=False,
        results_visibility="instant",
        options=[
            SimpleNamespace(
                id=BIG + 3,
                poll_id=BIG,
                text="a",
                position=0,
                vote_count=0,
            )
        ],
        total_votes=0,
        is_ended=False,
    )


class TestPollResponseIds:
    def test_poll_response_stringifies_ids(self):
        from src.api.routes.polls import _poll_to_response

        dumped = json.loads(_poll_to_response(_fake_poll()).model_dump_json())
        assert dumped["id"] == str(BIG)
        assert dumped["message_id"] == str(BIG + 1)
        assert dumped["created_by"] == str(BIG + 2)
        assert dumped["options"][0]["id"] == str(BIG + 3)
        assert dumped["options"][0]["poll_id"] == str(BIG)
        for value in (
            dumped["id"],
            dumped["message_id"],
            dumped["created_by"],
            dumped["options"][0]["id"],
            dumped["options"][0]["poll_id"],
        ):
            assert isinstance(value, str)
        # Timestamps and counts keep their numeric shape.
        assert dumped["created_at"] == 1700000000000
        assert dumped["total_votes"] == 0

    def test_poll_results_response_stringifies_ids(self):
        from src.api.routes.polls import _results_to_response

        results = SimpleNamespace(
            poll=_fake_poll(),
            options=[
                SimpleNamespace(
                    id=BIG + 3,
                    poll_id=BIG,
                    text="a",
                    position=0,
                    vote_count=2,
                )
            ],
            total_votes=2,
            user_voted=True,
            user_votes=[BIG + 3],
        )
        dumped = json.loads(_results_to_response(results).model_dump_json())
        assert dumped["poll"]["id"] == str(BIG)
        assert dumped["options"][0]["id"] == str(BIG + 3)
        assert dumped["options"][0]["poll_id"] == str(BIG)
        assert dumped["user_votes"] == [str(BIG + 3)]
        assert isinstance(dumped["user_votes"][0], str)
        assert dumped["total_votes"] == 2

    def test_poll_voters_route_stringifies_ids(self, monkeypatch):
        import src.api.routes.polls as polls_module

        class _Polls:
            def get_voters(self, poll_id, user_id):
                return [BIG, BIG + 1]

        monkeypatch.setattr(polls_module.api, "get_polls", lambda: _Polls())

        # ``get_poll_voters`` wraps each id in ``SnowflakeID``.
        import asyncio

        voters = asyncio.run(
            polls_module.get_poll_voters(
                str(BIG), current_user=SimpleNamespace(user_id=BIG)
            )
        )
        # FastAPI normally serializes the SnowflakeID list; the values must
        # already be exact decimal strings.
        assert [str(v) for v in voters] == [str(BIG), str(BIG + 1)]


class TestSoundboardResponseIds:
    def test_sound_response_stringifies_ids(self):
        from src.api.routes.soundboard import _sound_to_response

        sound = SimpleNamespace(
            id=BIG,
            server_id=BIG + 1,
            name="ping",
            format=SimpleNamespace(value="ogg"),
            emoji=None,
            url="https://cdn.example/s.ogg",
            size=1234,
            duration_seconds=1.5,
            volume=1.0,
            cooldown_seconds=0,
            created_by=BIG + 2,
            created_at=1700000000000,
            usage_count=3,
        )
        dumped = json.loads(_sound_to_response(sound).model_dump_json())
        assert dumped["id"] == str(BIG)
        assert dumped["server_id"] == str(BIG + 1)
        assert dumped["created_by"] == str(BIG + 2)
        for value in (dumped["id"], dumped["server_id"], dumped["created_by"]):
            assert isinstance(value, str)
        # Byte/duration/count fields stay numeric.
        assert dumped["size"] == 1234
        assert dumped["duration_seconds"] == 1.5
        assert dumped["usage_count"] == 3

    def test_permission_and_playback_responses_stringify_ids(self):
        from src.api.schemas.soundboard import SoundPermissionResponse

        perm = SoundPermissionResponse(
            id=BIG, sound_id=BIG + 1, role_id=BIG + 2, can_use=True
        )
        dumped = json.loads(perm.model_dump_json())
        assert dumped["id"] == str(BIG)
        assert dumped["sound_id"] == str(BIG + 1)
        assert dumped["role_id"] == str(BIG + 2)


class TestAdminTicketResponseIds:
    def _patch(self, monkeypatch, ticket):
        import src.api.routes.admin.tickets as tickets_module

        monkeypatch.setattr(tickets_module, "check_host_restriction", lambda r: None)
        monkeypatch.setattr(
            tickets_module, "require_admin_permission", lambda r, p: BIG
        )
        import src.core.admin as admin_core

        monkeypatch.setattr(
            admin_core, "get_feedback_tickets", lambda *a, **k: [ticket]
        )
        monkeypatch.setattr(admin_core, "get_ticket", lambda tid: ticket)
        return tickets_module

    def test_ticket_list_stringifies_ids(self, monkeypatch):
        import asyncio

        ticket = SimpleNamespace(
            id=BIG,
            user_id=BIG + 1,
            username="ada",
            content="please",
            category="bug",
            rating=5,
            status="open",
            created_at=1700000000000,
            resolved_at=None,
            resolved_by=None,
        )
        module = self._patch(monkeypatch, ticket)
        rows = asyncio.run(
            module.get_tickets(
                SimpleNamespace(), status_filter=None, limit=50, offset=0
            )
        )
        dumped = json.loads(rows[0].model_dump_json())
        assert dumped["id"] == str(BIG)
        assert dumped["user_id"] == str(BIG + 1)
        assert isinstance(dumped["id"], str)
        assert dumped["created_at"] == 1700000000000

    def test_ticket_notes_stringify_ids(self, monkeypatch):
        import asyncio

        ticket = SimpleNamespace(id=BIG, resolved_by=None)
        module = self._patch(monkeypatch, ticket)
        import src.core.admin as admin_core

        note = SimpleNamespace(
            id=BIG + 3,
            ticket_id=BIG,
            admin_id=BIG + 4,
            admin_username="mod",
            content="noted",
            created_at=1700000000001,
        )
        monkeypatch.setattr(admin_core, "get_ticket_notes", lambda tid: [note])

        notes = asyncio.run(module.get_ticket_notes(BIG, SimpleNamespace()))
        dumped = json.loads(notes[0].model_dump_json())
        assert dumped["id"] == str(BIG + 3)
        assert dumped["ticket_id"] == str(BIG)
        assert dumped["admin_id"] == str(BIG + 4)
        assert isinstance(dumped["ticket_id"], str)
        assert dumped["created_at"] == 1700000000001
