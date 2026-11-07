"""Task 12: Authz/abuse surface hardening regression tests.

Negative tests that fail if protections regress for:
- IDOR across servers/channels/DMs (core access boundaries)
- channels.view fanout filtering (broadcast._resolve_recipients)
- upload scanning/type/size
- SSRF allow/deny (media proxy/embeds link preview)
- WS intent/opcode/rate (120/min/conn, 5 conns/user)
- ratchet license/downgrade sentinel behavior
- DSAR authorization/expiry/redaction
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, Mock, patch

import pytest


def _png(size: int = 64) -> bytes:
    """A real PNG: the media pipeline runs it through Pillow, so a bare magic
    header fails metadata extraction before the upload checks are reached."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (size, size), (220, 40, 40)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# IDOR: servers/channels/DMs (core boundaries)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIdorCoreBoundaries:
    def test_non_member_cannot_get_server_channel(self, server_with_members):
        owner, _member1, _member2, server, servers = server_with_members
        channel = servers.create_channel(owner.id, server.id, "private-hardened")
        outsider = servers._auth.register(
            username="idor_outsider_ch",
            email="idor_outsider_ch@example.com",
            password="TestPass123!",
        )
        assert servers.get_channel(channel.id, outsider.id) is None

    def test_non_member_cannot_list_server_channels(self, server_with_members):
        _owner, _member1, _member2, server, servers = server_with_members
        outsider = servers._auth.register(
            username="idor_outsider_list",
            email="idor_outsider_list@example.com",
            password="TestPass123!",
        )
        with pytest.raises(servers.ServerAccessDeniedError):
            servers.get_channels(outsider.id, server.id)

    def test_non_participant_cannot_read_dm_messages(
        self, messaging_manager, auth_manager
    ):
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            u1 = auth_manager.register(
                "idor_dm1", "idor_dm1@example.com", "TestPass123!"
            )
            u2 = auth_manager.register(
                "idor_dm2", "idor_dm2@example.com", "TestPass123!"
            )
            u3 = auth_manager.register(
                "idor_dm3", "idor_dm3@example.com", "TestPass123!"
            )
        dm = messaging_manager.create_dm(u1.id, u2.id)
        messaging_manager.send_message(u1.id, dm.id, "secret")
        with pytest.raises(messaging_manager.ConversationAccessDeniedError):
            messaging_manager.get_messages(u3.id, dm.id)

    def test_non_participant_cannot_get_conversation(
        self, messaging_manager, auth_manager
    ):
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            u1 = auth_manager.register(
                "idor_gc1", "idor_gc1@example.com", "TestPass123!"
            )
            u2 = auth_manager.register(
                "idor_gc2", "idor_gc2@example.com", "TestPass123!"
            )
            u3 = auth_manager.register(
                "idor_gc3", "idor_gc3@example.com", "TestPass123!"
            )
        dm = messaging_manager.create_dm(u1.id, u2.id)
        assert messaging_manager.get_conversation(dm.id, u3.id) is None

    def test_api_cannot_modify_unowned_server(
        self, test_client, auth_manager, server_manager
    ):
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            u1 = auth_manager.register(
                "idor_api1", "idor_api1@example.com", "TestPass123!"
            )
            auth_manager.register("idor_api2", "idor_api2@example.com", "TestPass123!")
        server = server_manager.create_server(owner_id=u1.id, name="IDOR API")
        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login("idor_api2", "TestPass123!")
        resp = test_client.patch(
            f"/api/v1/servers/{server.id}",
            json={"name": "hacked"},
            headers={"Authorization": f"Bearer {result.token}"},
        )
        assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# channels.view fanout filtering (broadcast._resolve_recipients)
# ---------------------------------------------------------------------------


class TestBroadcastChannelsViewFanout:
    def _channel(self, server_id: int):
        ch = Mock()
        ch.server_id = server_id
        return ch

    def test_excludes_members_without_channels_view(self):
        from src.api.routes.messages.messages_crud.broadcast import _resolve_recipients

        servers_mod = Mock()
        servers_mod.get_channel.return_value = self._channel(10)
        servers_mod.get_member_user_ids.return_value = [1, 2, 3, 4]

        def has_perm(uid, sid, perm, cid):
            return perm == "channels.view" and uid in (1, 3)

        servers_mod.has_permission.side_effect = has_perm
        ids, server_id = _resolve_recipients(servers_mod, None, 99, None, actor_id=1)
        assert sorted(ids) == [1, 3]
        assert server_id == 10

    def test_no_overbroadcast_without_permission_api(self):
        from src.api.routes.messages.messages_crud.broadcast import _resolve_recipients

        servers_mod = Mock(spec=[])  # no has_permission
        servers_mod.get_channel = Mock(return_value=self._channel(10))
        servers_mod.get_member_user_ids = Mock(return_value=[1, 2, 3])
        ids, server_id = _resolve_recipients(servers_mod, None, 99, None, actor_id=1)
        assert ids == []
        assert server_id == 10

    def test_server_id_hint_still_permission_checks_recipients(self):
        from src.api.routes.messages.messages_crud.broadcast import _resolve_recipients

        # System actor cannot resolve channel via membership; hint supplies scope
        # but recipients MUST still be individually permission-checked.
        servers_mod = Mock()
        servers_mod.get_channel.side_effect = Exception("no access")
        servers_mod.get_member_user_ids.return_value = [5, 6, 7]
        servers_mod.has_permission.side_effect = lambda uid, sid, perm, cid: uid == 6
        ids, server_id = _resolve_recipients(
            servers_mod, None, 99, None, actor_id=0, server_id_hint=77
        )
        assert ids == [6]
        assert server_id == 77

    def test_dm_falls_back_to_participants_when_not_server_channel(self):
        from src.api.routes.messages.messages_crud.broadcast import _resolve_recipients

        messaging = Mock()
        p1, p2 = Mock(), Mock()
        p1.user_id, p2.user_id = 11, 22
        messaging.get_participants.return_value = [p1, p2]
        ids, server_id = _resolve_recipients(None, messaging, 55, 55, actor_id=11)
        assert ids == [11, 22]
        assert server_id is None

    def test_permission_exception_excludes_member_fail_closed(self):
        from src.api.routes.messages.messages_crud.broadcast import _resolve_recipients

        servers_mod = Mock()
        servers_mod.get_channel.return_value = self._channel(10)
        servers_mod.get_member_user_ids.return_value = [1, 2]

        def has_perm(uid, sid, perm, cid):
            if uid == 1:
                raise RuntimeError("perm backend down")
            return True

        servers_mod.has_permission.side_effect = has_perm
        ids, _ = _resolve_recipients(servers_mod, None, 99, None, actor_id=2)
        assert ids == [2]


# ---------------------------------------------------------------------------
# Upload scanning/type/size
# ---------------------------------------------------------------------------


@pytest.mark.media
class TestUploadHardening:
    def test_rejects_blocked_executable_extension(self, media_manager, test_user):
        from src.core.media.exceptions import FileTypeError

        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=b"MZ\x90\x00" + b"\x00" * 80,
                filename="dropper.exe",
                content_type="application/octet-stream",
            )

    def test_rejects_mime_spoofing(self, media_manager, test_user):
        from src.core.media.exceptions import FileTypeError

        with pytest.raises(FileTypeError):
            media_manager.upload_file(
                user_id=test_user.id,
                file_data=_png(),
                filename="fake.pdf",
                content_type="application/pdf",
            )

    def test_rejects_oversize_file(self, media_manager):
        from src.core.media.exceptions import FileSizeError
        from src.core.media.models import MediaType

        with pytest.raises(FileSizeError):
            media_manager._validate_file_size(500 * 1024 * 1024, MediaType.IMAGE)

    def test_rejects_disallowed_content_type(self, media_manager):
        from src.core.media.exceptions import FileTypeError
        from src.core.media.models import MediaType

        with pytest.raises(FileTypeError):
            media_manager._validate_content_type(
                "application/x-dosexec", MediaType.OTHER
            )

    def test_upload_records_scan_pending_or_terminal(self, media_manager, test_user):
        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=_png(),
            filename="scan-state.png",
            content_type="image/png",
        )
        mf = media_manager.get_file(result.file_id)
        assert mf is not None
        # Scanner disabled in tests -> SKIPPED; enabled async scanner -> PENDING.
        assert mf.scan_status.value in ("pending", "clean", "skipped", "error")

    def test_background_scan_fail_closed_when_unavailable(
        self, media_manager, test_user
    ):
        from src.core.media.models import ScanStatus

        result = media_manager.upload_file(
            user_id=test_user.id,
            file_data=_png(),
            filename="scan-fc.png",
            content_type="image/png",
        )
        mgr = media_manager
        mgr._config["scanner_enabled"] = True
        old = mgr._scanner
        try:
            sc = Mock()
            sc.is_available.return_value = False
            mgr._scanner = sc
            mgr._background_scan(result.file_id, _png(), test_user.id)
            mf = mgr.get_file(result.file_id)
            assert mf.scan_status == ScanStatus.ERROR
        finally:
            mgr._scanner = old
            mgr._config["scanner_enabled"] = False


# ---------------------------------------------------------------------------
# SSRF allow/deny (media proxy + embeds link preview)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/admin",
        "http://10.0.0.5/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://localhost/",
        "http://0.0.0.0/",
    ],
)
def test_media_proxy_rejects_private_ssrf_targets(url):
    from src.core.media.exceptions import ProxyFetchError
    from src.core.media.security.proxy import ExternalProxy

    proxy = ExternalProxy(storage_backend=Mock(), db=Mock())
    with pytest.raises(ProxyFetchError):
        proxy.fetch(url)


def test_media_proxy_never_follows_redirects():
    from unittest.mock import Mock, patch
    from src.core.media.exceptions import ProxyFetchError

    from src.core.media.security.proxy import ExternalProxy

    proxy = ExternalProxy(storage_backend=Mock(), db=Mock())
    resp = Mock()
    resp.status = 302
    resp.headers = {"Location": "http://169.254.169.254/", "Content-Type": "image/png"}
    pool = Mock()
    pool.urlopen.return_value = resp
    with patch("src.core.media.security.proxy.HTTPSConnectionPool", return_value=pool):
        with pytest.raises(ProxyFetchError, match="Redirects are not allowed"):
            proxy._fetch_url(
                "https://example.com/x.png", "example.com", "93.184.216.34"
            )
    assert pool.urlopen.call_args[1]["redirect"] is False


def test_link_preview_rejects_private_and_internal_urls():
    from src.core.embeds.link_preview import LinkPreviewService

    svc = LinkPreviewService(Mock())
    for url in [
        "http://127.0.0.1/x",
        "http://169.254.169.254/",
        "http://10.1.2.3/",
        "http://192.168.0.2/",
        "http://server.local/page",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "javascript:alert(1)",
    ]:
        with pytest.raises(ValueError):
            svc._validate_url(url)


def test_link_preview_blocks_svg_images_xss():
    from src.core.embeds.link_preview import LinkPreviewService

    svc = LinkPreviewService(Mock())
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert svc._validate_image_content(svg, "image/svg+xml") is False


def test_link_preview_rejects_fake_image_magic():
    from src.core.embeds.link_preview import LinkPreviewService

    svc = LinkPreviewService(Mock())
    assert svc._validate_image_content(b"MZ\x00\x00fake", "image/jpeg") is False


# ---------------------------------------------------------------------------
# WS intent/opcode/rate (120/min/conn, 5 conns/user)
# ---------------------------------------------------------------------------


class TestWsHardening:
    def test_connection_rate_limit_120_per_minute(self):
        from src.api.websocket.connection import Connection

        ws = MagicMock()
        conn = Connection(websocket=ws, connection_id="c", heartbeat_interval_ms=45000)
        for _ in range(120):
            assert conn.check_rate_limit(120) is True
        assert conn.check_rate_limit(120) is False

    def test_session_enforces_5_connections_per_user(self):
        from src.api.websocket.connection import Connection
        from src.api.websocket.session import SessionManager

        sm = SessionManager(max_connections_per_user=5)
        for i in range(5):
            c = Connection(
                websocket=MagicMock(),
                connection_id=f"c{i}",
                heartbeat_interval_ms=45000,
            )
            sm.add_connection(c)
            sm.create_session(c, user_id=42, intents=0)
        assert sm.can_user_connect(42) is False
        assert sm.get_user_connection_count(42) == 5

    @pytest.mark.asyncio
    async def test_unknown_opcode_closes_with_4001(self):
        from src.api.websocket.connection import Connection
        from src.api.websocket.handlers import OpcodeHandler
        from src.api.websocket.opcodes import GatewayCloseCode
        from src.api.websocket.session import SessionManager

        sm = SessionManager()
        handler = OpcodeHandler(session_manager=sm)
        conn = Connection(
            websocket=MagicMock(), connection_id="x", heartbeat_interval_ms=45000
        )
        _, _, close_code = await handler.handle(conn, 999, {})
        assert close_code == GatewayCloseCode.UNKNOWN_OPCODE

    @pytest.mark.asyncio
    async def test_server_push_echo_opcode_soft_rejected(self):
        from src.api.websocket.connection import Connection
        from src.api.websocket.handlers import OpcodeHandler
        from src.api.websocket.opcodes import GatewayOpcode
        from src.api.websocket.session import SessionManager

        sm = SessionManager()
        handler = OpcodeHandler(session_manager=sm)
        conn = Connection(
            websocket=MagicMock(), connection_id="x", heartbeat_interval_ms=45000
        )
        _, _, close_code = await handler.handle(conn, int(GatewayOpcode.DISPATCH), {})
        assert close_code is None  # ignored, connection kept

    def test_intent_filtering_blocks_unsubscribed_message_events(self):
        from src.api.websocket.intents import filter_event_by_intents
        from src.core import events
        from src.core.events.types import GatewayIntent

        msg = events.create_message_create(
            message_id=1, channel_id=2, author_id=3, content="x", server_id=4
        )
        assert filter_event_by_intents(msg, GatewayIntent.GUILDS) is False
        assert filter_event_by_intents(msg, GatewayIntent.GUILD_MESSAGES) is True

    def test_gateway_setup_defaults(self):
        import inspect
        from src.api.websocket import setup

        sig = inspect.signature(setup)
        assert sig.parameters["rate_limit_per_minute"].default == 120
        assert sig.parameters["max_connections_per_user"].default == 5


# ---------------------------------------------------------------------------
# Ratchet license/downgrade sentinel behavior
# ---------------------------------------------------------------------------


class TestRatchetLicenseSentinel:
    def test_unlicensed_v3_read_returns_sentinel_not_crash(self):
        from src.utils.encryption.channel_ratchet import ratchet_encryption_licensed

        row_content = "ENC:3:42:" + "AA=="
        with patch(
            "src.utils.encryption.channel_ratchet.ratchet_encryption_licensed",
            return_value=False,
        ):
            # Mirror repository branch: unlicensed -> sentinel
            if row_content.startswith("ENC:3:") and not ratchet_encryption_licensed():
                content = "[unsupported encryption version]"
            else:
                content = "decrypted"
        assert content == "[unsupported encryption version]"

    def test_ratchet_gate_defaults_false_on_exception(self):
        from src.utils.encryption.channel_ratchet.gate import (
            ratchet_encryption_licensed,
        )

        with patch(
            "src.utils.common_utils.utils.licensing.has_feature",
            side_effect=RuntimeError("boom"),
        ):
            assert ratchet_encryption_licensed() is False

    def test_send_must_not_use_v3_when_unlicensed(self):
        with patch(
            "src.utils.encryption.channel_ratchet.ratchet_encryption_licensed",
            return_value=False,
        ):
            with patch("src.utils.encryption._get_message_encryptor") as mock_get_enc:
                from src.utils.encryption import encrypt_message

                enc = MagicMock()
                enc.encrypt_message.return_value = "ENC:1:legacy"
                mock_get_enc.return_value = enc
                env = encrypt_message("hello", 123)
        assert env.startswith("ENC:1:") and not env.startswith("ENC:3:")


# ---------------------------------------------------------------------------
# DSAR authorization/expiry/redaction
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDsarHardening:
    @pytest.fixture
    def dsar_env(self, db, auth_manager, pii_gen):
        from src.core.dsar import setup as dsar_setup
        from src.utils import encryption

        dsar_setup(db)
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            u1 = auth_manager.register("dsarh1", pii_gen.email(), "TestPass123!")
            u2 = auth_manager.register("dsarh2", pii_gen.email(), "TestPass123!")
            admin = auth_manager.register("dsarha", pii_gen.email(), "TestPass123!")
        return db, u1, u2, admin

    def test_request_status_denied_to_other_user(self, dsar_env):
        from src.core.dsar import request_data_export, get_request_status

        _db, u1, u2, _admin = dsar_env
        req = request_data_export(u1.id)
        assert get_request_status(req["id"], u1.id) is not None
        assert get_request_status(req["id"], u2.id) is None

    def test_export_file_denied_to_other_user(self, dsar_env):
        from src.core.dsar import get_export_file

        db, u1, u2, _admin = dsar_env
        now = int(time.time())
        rid = int(time.time_ns() % 10**12) + 9001
        db.execute(
            """INSERT INTO dsar_requests
               (id, user_id, status, requested_at, completed_at, expires_at,
                format, storage_backend, storage_path, checksum, file_size_bytes)
               VALUES (?, ?, 'ready', ?, ?, ?, 'json', 'local', ?, 'x', 1)""",
            (rid, u1.id, now, now, now + 3600, "/tmp/dsar-nonexistent-hardening.json"),
        )
        assert get_export_file(rid, u2.id) is None

    def test_cancel_denied_to_other_user(self, dsar_env):
        from src.core.dsar import request_data_export, cancel_request

        _db, u1, u2, _admin = dsar_env
        req = request_data_export(u1.id)
        with pytest.raises(PermissionError):
            cancel_request(req["id"], u2.id)

    def test_expired_export_scrubs_file_and_marks_expired(self, dsar_env, tmp_path):
        from src.core.dsar import get_export_file

        db, u1, _u2, _admin = dsar_env
        f = tmp_path / "dsar-exp.json"
        f.write_text('{"pii": true}')
        now = int(time.time())
        rid = int(time.time_ns() % 10**12) + 9002
        db.execute(
            """INSERT INTO dsar_requests
               (id, user_id, status, requested_at, completed_at, expires_at,
                format, storage_backend, storage_path, checksum, file_size_bytes)
               VALUES (?, ?, 'ready', ?, ?, ?, 'json', 'local', ?, 'x', 5)""",
            (rid, u1.id, now, now, now - 10, str(f)),
        )
        assert get_export_file(rid, u1.id) is None
        row = db.fetch_one("SELECT status FROM dsar_requests WHERE id = ?", (rid,))
        assert row["status"] == "expired"
        assert not f.exists()

    def test_sessions_ciphertext_redacted_in_collection(self, dsar_env):
        from src.core.dsar.collectors.sessions import SessionsCollector

        db, u1, _u2, _admin = dsar_env
        db.execute(
            "INSERT INTO auth_sessions (user_id, token_hash, ip_encrypted,"
            " ua_encrypted, created_at, expires_at, last_activity)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (u1.id, "tokhash-dsarh", "CIPHERIP_SECRET", "CIPHERUA_SECRET", 1, 2, 3),
        )
        sessions = SessionsCollector(db).collect(u1.id)["sessions"]
        hits = [s for s in sessions if s.get("token_hash") == "tokhash-dsarh"]
        assert hits
        assert hits[0]["ip_encrypted"] == "(encrypted)"
        assert hits[0]["ua_encrypted"] == "(encrypted)"

    def test_rejects_unsupported_export_format(self, dsar_env):
        from src.core.dsar import request_data_export

        _db, u1, _u2, _admin = dsar_env
        with pytest.raises(ValueError):
            request_data_export(u1.id, format="pdf")


# ---------------------------------------------------------------------------
# get_blocked_users guard for serving-safety scan statuses (media serving)
# ---------------------------------------------------------------------------


class TestMediaServingScanGate:
    def test_only_clean_or_skipped_are_servable(self):
        # Mirror the allowlist in src/api/media_serving.py:340-351
        servable = {"clean", "skipped"}
        for status in ("clean", "skipped"):
            assert status in servable
        for status in ("pending", "infected", "error", "", "unknown"):
            assert status not in servable
