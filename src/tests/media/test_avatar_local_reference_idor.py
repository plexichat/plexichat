"""A user avatar URL must not publish another user's private attachment.

``MediaManager.check_file_access`` grants access to a file when any
``auth_users.avatar_url`` local-references that file's name. The sibling
server-icon branch was hardened to require that the server owner actually
uploaded the referenced file, but the avatar branch was left with a bare
local-reference match. A user who can set their own ``avatar_url`` to a local
media path can therefore point it at somebody else's private attachment and
grant themselves (and everyone else) a download, with no per-file ownership
or sharing relationship.
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


def test_local_avatar_reference_cannot_publish_other_users_private_file(
    server_with_members, media_manager
):
    owner, member, victim, _server, _servers = server_with_members

    # The victim uploads a private file. The attacker (a server owner) has no
    # relationship to it.
    upload = media_manager.upload_file(
        user_id=victim.id,
        file_data=MINI_PNG,
        filename="victim_private_avatar.png",
        content_type="image/png",
    )
    target = media_manager.get_file(upload.file_id)
    assert target is not None
    assert media_manager.check_file_access(target.filename, owner.id) is False
    assert media_manager.check_file_access(target.filename, member.id) is False

    # The attacker points their own avatar at the victim's private attachment
    # using a same-origin local media path. This is the client-controlled
    # avatar_url value the access check trusts.
    avatar_url = f"/api/v1/media/attachments/{target.filename}"
    media_manager._db.execute(
        "UPDATE auth_users SET avatar_url = ? WHERE id = ?", (avatar_url, owner.id)
    )
    row = media_manager._db.fetch_one(
        "SELECT avatar_url FROM auth_users WHERE id = ?", (owner.id,)
    )
    assert (row["avatar_url"] if isinstance(row, dict) else row[0]) == avatar_url

    # A client-controlled avatar reference alone must not publish somebody
    # else's file.
    assert media_manager.check_file_access(target.filename, owner.id) is False
    assert media_manager.check_file_access(target.filename, member.id) is False


def test_owned_local_avatar_reference_still_marks_file_public(
    server_with_members, media_manager
):
    """The legitimate case (a user's own uploaded avatar) keeps working."""
    owner, member, _victim, _server, _servers = server_with_members

    upload = media_manager.upload_file(
        user_id=owner.id,
        file_data=MINI_PNG,
        filename="owner_own_avatar.png",
        content_type="image/png",
    )
    target = media_manager.get_file(upload.file_id)
    assert target is not None

    avatar_url = f"/api/v1/media/attachments/{target.filename}"
    media_manager._db.execute(
        "UPDATE auth_users SET avatar_url = ? WHERE id = ?", (avatar_url, owner.id)
    )
    assert media_manager.check_file_access(target.filename, member.id) is True
