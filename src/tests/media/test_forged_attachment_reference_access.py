"""Regression: a forged attachment reference must not grant access to a
private media file.

``MediaManager.check_file_access`` grants a user access to a file whenever any
message attachment whose ``metadata`` contains that file's id lives in a
conversation the user participates in. Attachment metadata is
attacker-controlled (``AttachmentRequest.metadata`` -> ``send_message`` ->
``msg_attachments.metadata``) and ``_reject_locked_media_reference`` accepts an
explicit ``file_id`` without verifying that the message author may access the
referenced file. A user can therefore post a message in a conversation they own
whose attachment metadata names a victim's private file, and the download gate
then treats that forged link as proof of legitimate access.

The test below drives the real DB-backed managers so the flaw is proven end to
end, not by mocking the access check.
"""

import pytest

from src.tests.media.test_access_control import MINI_PNG


@pytest.mark.media
def test_forged_attachment_metadata_does_not_grant_file_access(
    media_manager, messaging_manager, three_users
):
    victim, attacker, bystander = three_users

    # Victim uploads a private file in a conversation the attacker is NOT part
    # of. Baseline: the attacker has no relationship to the file.
    uploaded = media_manager.upload_file(
        user_id=victim.id,
        file_data=MINI_PNG,
        filename="forged_ref_victim_private.png",
        content_type="image/png",
    )
    target = media_manager.get_file(uploaded.file_id)
    assert target is not None
    assert media_manager.check_file_access(target.filename, attacker.id) is False

    # Attacker posts in a conversation they control (DM with a bystander) an
    # attachment whose client-supplied metadata claims the victim's file id.
    dm = messaging_manager.create_dm(attacker.id, bystander.id)
    messaging_manager.send_message(
        user_id=attacker.id,
        conversation_id=dm.id,
        content="look at this",
        attachments=[
            {
                "filename": "innocent.png",
                "content_type": "image/png",
                "size": 10,
                "url": "/api/v1/media/attachments/innocent.png",
                "metadata": {"file_id": target.id},
            }
        ],
    )

    # The forged link must NOT publish the victim's private attachment.
    assert media_manager.check_file_access(target.filename, attacker.id) is False
