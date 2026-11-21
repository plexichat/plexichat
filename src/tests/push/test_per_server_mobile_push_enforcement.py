"""A per-server ``mobile_push=False`` opt-out must reach the device-push path.

Notification push payloads carry the server the notification was generated
for (``data["server_id"]``), and notification preferences are per-server
capable. The push delivery manager used to resolve ``mobile_push`` from the
GLOBAL settings row only. A member who disabled mobile push for one server --
without a global settings row -- therefore still received a device push for a
mention in that server, silently ignoring their opt-out.

``send_push``/``send_push_async`` must resolve the preference at the same
server scope the payload came from. The per-server row wins; when the user has
no row for that server, the resolver falls back to the global row (see the
notification settings mixin), so global opt-outs keep working.
"""

from unittest.mock import patch

import pytest


@pytest.fixture
def push_mgr(db, notification_manager):
    """Push manager wired to the test notification manager with fake transport."""
    from src.core.push.manager import PushManager

    mgr = PushManager(db, notifications_module=notification_manager)
    # A registered device exists; platform delivery is faked so a successful
    # send is observable as a nonzero return value rather than a no-op caused
    # by missing provider credentials.
    mgr.get_user_tokens = lambda user_id, platform=None: [
        {"token": "web-token", "platform": "web", "p256dh": "p", "auth": "a"}
    ]
    mgr._send_to_platform = lambda *args, **kwargs: True
    return mgr


def test_per_server_mobile_push_opt_out_suppresses_push(db, modules, push_mgr):
    from src.utils import encryption

    auth = modules.auth
    servers = modules.servers
    notifications = modules.notifications

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        author = auth.register(
            username="mps_author",
            email="mps_author@example.com",
            password="TestPass123!",
        )
        muted = auth.register(
            username="mps_muted",
            email="mps_muted@example.com",
            password="TestPass123!",
        )
        control = auth.register(
            username="mps_control",
            email="mps_control@example.com",
            password="TestPass123!",
        )

    server = servers.create_server(author.id, "Mobile Push Scope Server")
    for uid in (muted.id, control.id):
        servers.add_member(server.id, uid)

    # The muted member opts out of mobile push for THIS server only and has no
    # global notification settings row. The control has no settings at all.
    notifications.update_notification_settings(
        muted.id, server_id=server.id, mobile_push=False
    )

    payload = {"notification_id": "1", "server_id": str(server.id)}

    # Control recipient proves the faked transport really delivers a push
    # (guards against a vacuous pass from missing provider config).
    assert push_mgr.send_push(control.id, "t", "b", data=payload) == 1
    # The member who opted out of push for this server must get no device push.
    assert push_mgr.send_push(muted.id, "t", "b", data=payload) == 0
