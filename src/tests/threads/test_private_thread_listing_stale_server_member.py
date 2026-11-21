"""Private-thread LISTINGS must not trust a stale ``thread_members`` row.

Leaving (or being kicked from) a server deletes the ``srv_members`` row but
does NOT prune ``thread_members``. ``can_view_thread``/``get_thread`` were
hardened to re-check the live server roster, but the listing paths still
hand a private thread back on the strength of the stale membership row
alone: ``get_user_threads`` / ``get_user_private_threads`` (the
``/users/@me/threads`` endpoints) and ``get_active_threads`` /
``get_archived_threads`` (the channel listing endpoints).

A former member therefore keeps enumerating private threads -- with their
name, owner, message/member counts and existence -- long after they have lost
all right to the parent channel. These tests pin the fail-closed roster check
at each listing path.
"""

from src.core.threads import ThreadType


def _ids(threads):
    return {t.id for t in threads}


def test_removed_server_member_hidden_from_private_thread_listings(
    server_with_channel,
):
    owner, member1, _member2, server, channel, servers, threads = server_with_channel

    private = threads.create_thread(
        user_id=owner.id,
        channel_id=channel.id,
        name="Secret Planning",
        thread_type=ThreadType.PRIVATE,
    )
    threads.add_member(owner.id, private.id, member1.id)

    # While still a server member, membership grants private-thread access in
    # every listing path.
    assert threads.can_view_thread(member1.id, private.id) is True
    assert private.id in _ids(threads.get_user_threads(member1.id))
    assert private.id in _ids(threads.get_user_private_threads(member1.id))
    assert private.id in _ids(threads.get_active_threads(member1.id, channel.id))

    # The member leaves the backing server; the thread_members row survives.
    assert servers.remove_member(member1.id, server.id) is True
    assert servers.get_member(server.id, member1.id) is None
    assert threads._is_member(member1.id, private.id) is True  # stale row

    # Fail closed: the stale row must no longer surface the thread anywhere.
    assert threads.can_view_thread(member1.id, private.id) is False
    assert private.id not in _ids(threads.get_user_threads(member1.id))
    assert private.id not in _ids(threads.get_user_private_threads(member1.id))
    assert private.id not in _ids(threads.get_active_threads(member1.id, channel.id))


def test_removed_server_member_hidden_from_archived_private_thread_listing(
    server_with_channel,
):
    owner, member1, _member2, server, channel, servers, threads = server_with_channel

    private = threads.create_thread(
        user_id=owner.id,
        channel_id=channel.id,
        name="Secret Archived",
        thread_type=ThreadType.PRIVATE,
    )
    threads.add_member(owner.id, private.id, member1.id)
    # Archive directly so it shows up in the archived listing.
    threads.archive_thread(owner.id, private.id)

    assert private.id in _ids(
        threads.get_archived_threads(member1.id, channel.id, limit=200)
    )

    assert servers.remove_member(member1.id, server.id) is True
    assert threads._is_member(member1.id, private.id) is True  # stale row

    assert private.id not in _ids(
        threads.get_archived_threads(member1.id, channel.id, limit=200)
    )
