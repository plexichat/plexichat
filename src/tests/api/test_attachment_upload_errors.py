"""Upload error mapping for the attachment route.

A client-supplied file that cannot be decoded is the client's problem, so it
must come back as a 400.  It used to fall through to the route's generic
handler and surface as "Internal server error" (500), which reads like a
server fault in the client and in monitoring.
"""

# Truncated 1x1 PNG: the IDAT chunk declares 13 bytes but carries 12, so the
# decoder rejects the file.  This is the exact payload that produced a 500
# against the live deployment.
BROKEN_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6360000000020001558f2d7f0000000049454e44ae426082"
)


def test_undecodable_image_is_a_400_not_a_500(
    test_client, modules, test_server, test_user_with_token
):
    server, _user = test_server
    channel = modules.servers.get_channels(server.owner_id, server.id)[0]
    token = test_user_with_token["token"]

    response = test_client.post(
        f"/api/v1/channels/{channel.id}/attachments",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("broken.png", BROKEN_PNG, "image/png")},
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == 400
