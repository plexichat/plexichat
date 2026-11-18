"""Regression: an attacker-controlled avatar/icon URL must not publish a
private attachment to other users.

``MediaManager.check_file_access`` granted access to a file when any
``auth_users.avatar_url`` or ``srv_servers.icon_url`` ended with that file's
name (``LIKE '%' || filename``). Server icon URLs are settable through the
public server-create/update API as absolute http(s) URLs, so a server owner
could point their icon at a private attachment's filename and every member of
their server would suddenly be allowed to download that attachment through
``/api/v1/media/attachments/{filename}``.
"""

import pytest

from src.tests.media.test_access_control import MINI_PNG


@pytest.mark.media
def test_external_icon_url_cannot_publish_private_attachment(
    media_manager, server_with_members
):
    owner, member, _other, server, _servers = server_with_members
    result = media_manager.upload_file(
        user_id=owner.id,
        file_data=MINI_PNG,
        filename="private_icon_bypass.png",
        content_type="image/png",
    )
    target = media_manager.get_file(result.file_id)
    assert target is not None

    # Baseline: a server member with no relationship to the uploader has no
    # access to the private attachment.
    assert media_manager.check_file_access(target.filename, member.id) is False

    # The attacker controls this server's icon URL; the schema accepts an
    # absolute URL on a public CDN. Before the fix the suffix-only comparison
    # treated it as a public reference to the private file.
    media_manager._db.execute(
        "UPDATE srv_servers SET icon_url = ? WHERE id = ?",
        (f"https://cdn.example.test/{target.filename}", server.id),
    )
    assert media_manager.check_file_access(target.filename, member.id) is False


@pytest.mark.media
def test_local_media_reference_still_marks_file_public(
    media_manager, server_with_members
):
    """The fix must not revoke legitimate local avatar/icon references."""
    owner, member, _other, server, _servers = server_with_members
    result = media_manager.upload_file(
        user_id=owner.id,
        file_data=MINI_PNG,
        filename="public_local_ref.png",
        content_type="image/png",
    )
    target = media_manager.get_file(result.file_id)
    assert target is not None

    media_manager._db.execute(
        "UPDATE srv_servers SET icon_url = ? WHERE id = ?",
        (f"/api/v1/media/attachments/{target.filename}", server.id),
    )
    assert media_manager.check_file_access(target.filename, member.id) is True


@pytest.mark.media
def test_external_avatar_url_cannot_publish_private_attachment(
    media_manager, server_with_members
):
    """The same flaw existed for ``auth_users.avatar_url``."""
    owner, _member, _other, _server, _servers = server_with_members
    result = media_manager.upload_file(
        user_id=owner.id,
        file_data=MINI_PNG,
        filename="private_avatar_bypass.png",
        content_type="image/png",
    )
    target = media_manager.get_file(result.file_id)
    assert target is not None

    media_manager._db.execute(
        "UPDATE auth_users SET avatar_url = ? WHERE id = ?",
        (f"https://cdn.example.test/{target.filename}", owner.id),
    )
    unrelated_id = owner.id + 1000000
    assert media_manager.check_file_access(target.filename, unrelated_id) is False
