"""A server icon URL must not publish another user's private attachment.

``MediaManager.check_file_access`` grants every member of a server access to a
file whose filename is the final path segment of that server's ``icon_url``,
as long as the URL is a same-origin path. ``validate_server_media_url`` accepts
any same-origin path, including ``/api/v1/media/attachments/<filename>``. A
server owner can therefore point their icon at a private attachment uploaded
by somebody else and grant themselves (and every server member) a download,
with only a server-membership ("participation") check and no per-file
ownership or sharing relationship.
"""

import pytest

from src.tests.media.test_access_control import MINI_PNG

pytestmark = [pytest.mark.media, pytest.mark.security]


@pytest.fixture(autouse=True)
def _licensing_ready_for_registration():
    """The user fixtures register accounts, which consults licensing limits."""
    import src.utils.common_utils.utils.licensing as licensing

    before = (
        getattr(licensing, "_setup_called", False),
        getattr(licensing, "_free_tier_mode", None),
    )
    licensing.setup()
    licensing._free_tier_mode = True
    yield
    licensing._setup_called, licensing._free_tier_mode = before


def test_local_icon_reference_cannot_publish_other_users_private_file(
    server_with_members, media_manager, server_manager, db
):
    owner, member, victim, server, _servers = server_with_members

    # The victim uploads a private file. Neither the server owner nor its
    # member has any relationship to it.
    upload = media_manager.upload_file(
        user_id=victim.id,
        file_data=MINI_PNG,
        filename="victim_private_icon.png",
        content_type="image/png",
    )
    target = media_manager.get_file(upload.file_id)
    assert target is not None
    assert media_manager.check_file_access(target.filename, owner.id) is False
    assert media_manager.check_file_access(target.filename, member.id) is False

    # The server owner (attacker) points the server icon at the victim's file.
    # The core validator accepts this same-origin path verbatim; this is the
    # value the create/update-server API stores in srv_servers.icon_url.
    from src.core.servers.validation import validate_server_media_url

    icon_url = f"/api/v1/media/attachments/{target.filename}"
    assert validate_server_media_url(icon_url, "Server icon URL") == icon_url
    server_manager.update_server(owner.id, server.id, icon_url=icon_url)
    row = db.fetch_one("SELECT icon_url FROM srv_servers WHERE id = ?", (server.id,))
    assert (row["icon_url"] if isinstance(row, dict) else row[0]) == icon_url

    # Server membership alone must not confer access to somebody else's file.
    assert media_manager.check_file_access(target.filename, owner.id) is False
    assert media_manager.check_file_access(target.filename, member.id) is False


def test_owned_local_icon_reference_still_marks_file_public(
    server_with_members, media_manager, server_manager, db
):
    """The legitimate case (the server owner's own uploaded icon) keeps working."""
    owner, member, _victim, server, _servers = server_with_members

    upload = media_manager.upload_file(
        user_id=owner.id,
        file_data=MINI_PNG,
        filename="owner_icon.png",
        content_type="image/png",
    )
    target = media_manager.get_file(upload.file_id)
    assert target is not None

    icon_url = f"/api/v1/media/attachments/{target.filename}"
    server_manager.update_server(owner.id, server.id, icon_url=icon_url)
    row = db.fetch_one("SELECT icon_url FROM srv_servers WHERE id = ?", (server.id,))
    assert (row["icon_url"] if isinstance(row, dict) else row[0]) == icon_url
    assert media_manager.check_file_access(target.filename, member.id) is True
