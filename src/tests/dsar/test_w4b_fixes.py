"""
Regression tests for W4-B DSAR fixes.

Covers: H1 (collector column fixes + incomplete-export metadata),
M3 (cancel-vs-harvester race), M4 (expired export file deletion),
L2 (ciphertext redaction), L5 (audit chain reset fails loudly).
"""

import time

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def dsar_setup(db):
    from src.core.dsar import setup as dsar_setup

    dsar_setup(db)
    return db


@pytest.fixture
def seeded_user(dsar_setup, auth_manager, pii_gen):
    from unittest.mock import patch
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=f"w4buser_{time.time_ns()}",
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return user


@pytest.fixture
def conversation_and_message(db, seeded_user):
    uid = seeded_user.id
    now = int(time.time())
    db.execute(
        "INSERT INTO msg_conversations (id, conversation_type, owner_id,"
        " created_at, updated_at) VALUES (?, 'group', ?, ?, ?)",
        (880001, uid, now, now),
    )
    db.execute(
        "INSERT INTO msg_messages (id, conversation_id, author_id, content,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (880002, 880001, uid, "hello", now, now),
    )
    return uid


class TestH1CollectorColumns:
    """Collectors must query the REAL columns or categories vanish."""

    def test_pinned_collected_via_pinned_by(self, dsar_setup, conversation_and_message):
        db = dsar_setup
        uid = conversation_and_message
        db.execute(
            "INSERT INTO msg_pinned (conversation_id, message_id, pinned_by,"
            " pinned_at) VALUES (?, ?, ?, ?)",
            (880001, 880002, uid, int(time.time())),
        )
        from src.core.dsar.collectors.content import ContentCollector

        pins = ContentCollector(db)._collect_pinned(uid)
        assert len(pins) == 1
        assert pins[0]["pinned_by"] == uid

    def test_attachments_collected_via_message_author(
        self, dsar_setup, conversation_and_message
    ):
        db = dsar_setup
        uid = conversation_and_message
        db.execute(
            "INSERT INTO msg_attachments (message_id, filename, content_type,"
            " size, url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (880002, "a.png", "image/png", 10, "/u/a.png", int(time.time())),
        )
        from src.core.dsar.collectors.content import ContentCollector

        atts = ContentCollector(db)._collect_attachments(uid)
        assert len(atts) == 1
        assert atts[0]["filename"] == "a.png"

    def test_media_files_real_columns(self, dsar_setup, seeded_user):
        db = dsar_setup
        uid = seeded_user.id
        db.execute(
            "INSERT INTO media_files (filename, original_filename, content_type,"
            " size, media_type, storage_backend, storage_path, uploaded_by,"
            " uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "f.png",
                "o.png",
                "image/png",
                12,
                "image",
                "local",
                "p/f.png",
                uid,
                int(time.time()),
            ),
        )
        from src.core.dsar.collectors.media import MediaCollector

        media = MediaCollector(db)._collect_media_files(uid)
        assert len(media) == 1
        assert media[0]["original_filename"] == "o.png"
        assert "storage_path" not in media[0]

    def test_avatars_metadata_without_blob(self, dsar_setup, seeded_user):
        db = dsar_setup
        uid = seeded_user.id
        db.execute(
            "INSERT INTO user_avatars (id, user_id, avatar_data, content_type,"
            " width, height, size, checksum, uploaded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, uid, b"x", "image/png", 64, 64, 300, "cs", int(time.time())),
        )
        from src.core.dsar.collectors.media import MediaCollector

        avatars = MediaCollector(db)._collect_avatars(uid)
        assert len(avatars) == 1
        assert avatars[0]["checksum"] == "cs"
        assert "avatar_data" not in avatars[0]
        assert "avatar_url" not in avatars[0]

    def test_collection_error_surfaced_in_export_metadata(
        self, dsar_setup, monkeypatch
    ):
        from src.core.dsar.collector import DataCollector
        from src.core.dsar.collectors.content import ContentCollector

        def broken(self, user_id):
            self.record_error("pinned query exploded")
            return []

        monkeypatch.setattr(ContentCollector, "_collect_pinned", broken)
        result = DataCollector(dsar_setup).collect_all(42)
        meta = result.get("export_metadata")
        assert meta is not None and meta["complete"] is False
        assert any("pinned" in w for w in meta["warnings"])


class TestL2Redaction:
    def test_sessions_ciphertext_redacted(self, dsar_setup, seeded_user):
        db = dsar_setup
        uid = seeded_user.id
        db.execute(
            "INSERT INTO auth_sessions (user_id, token_hash, ip_encrypted,"
            " ua_encrypted, created_at, expires_at, last_activity)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, "tokhash", "CIPHERIP", "CIPHERUA", 1, 2, 3),
        )
        from src.core.dsar.collectors.sessions import SessionsCollector

        sessions = SessionsCollector(db).collect(uid)["sessions"]
        target = [s for s in sessions if s.get("ip_encrypted") is not None]
        assert target and any(s["ip_encrypted"] == "(encrypted)" for s in target)
        assert all(s["ua_encrypted"] == "(encrypted)" for s in target)

    def test_edit_history_ciphertext_redacted(
        self, dsar_setup, conversation_and_message
    ):
        db = dsar_setup
        uid = conversation_and_message
        db.execute(
            "INSERT INTO msg_edit_history (message_id, editor_id, old_content,"
            " old_content_encrypted, edit_timestamp, version_number)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (880002, uid, "old text", "CIPHER", 1, 1),
        )
        from src.core.dsar.collectors.messages import MessagesCollector

        history = MessagesCollector(db)._collect_edit_history(uid)
        assert history
        assert history[0]["old_content_encrypted"] == "(encrypted)"
        assert history[0]["old_content"] == "old text"


class TestM3CancelRace:
    def test_cancelled_request_not_resurrected_by_harvester(self, dsar_setup):
        from src.core.dsar.harvester import DSARHarvester

        db = dsar_setup
        req_id = int(time.time_ns() % 10**12)
        db.execute(
            "INSERT INTO dsar_requests (id, user_id, status, requested_at, format)"
            " VALUES (?, ?, 'cancelled', ?, 'json')",
            (req_id, 42, int(time.time())),
        )

        harvester = DSARHarvester(db)
        harvester._process_request(
            dict(db.fetch_one("SELECT * FROM dsar_requests WHERE id = ?", (req_id,)))
        )

        row = db.fetch_one("SELECT status FROM dsar_requests WHERE id = ?", (req_id,))
        assert row["status"] == "cancelled"

    def test_cancel_during_generation_deletes_artifact(self, dsar_setup, tmp_path):
        """Completion must be conditional; cancelled rows lose the artifact."""
        import src.core.dsar.harvester as hv
        from src.core.dsar.harvester import DSARHarvester

        db = dsar_setup
        artifact = tmp_path / "artifact.json"
        artifact.write_text('{"pii": true}')
        req_id = int(time.time_ns() % 10**12) + 1
        db.execute(
            "INSERT INTO dsar_requests (id, user_id, status, requested_at, format)"
            " VALUES (?, ?, 'approved', ?, 'json')",
            (req_id, 42, int(time.time())),
        )

        deleted = []

        class FakeGenerator:
            backend_name = "local"

            def __init__(self, db=None):
                pass

            def generate_json(self, data, request_id, user_id):
                # Simulate the user cancelling WHILE generation runs.
                db.execute(
                    "UPDATE dsar_requests SET status = 'cancelled' WHERE id = ?",
                    (request_id,),
                )
                return str(artifact), "cs", artifact.stat().st_size

            def delete(self, path):
                deleted.append(path)
                return True

        harvester = DSARHarvester(db)
        orig_gen = hv.ExportFormatGenerator
        hv.ExportFormatGenerator = FakeGenerator
        try:
            harvester._process_request(
                dict(
                    db.fetch_one("SELECT * FROM dsar_requests WHERE id = ?", (req_id,))
                )
            )
        finally:
            hv.ExportFormatGenerator = orig_gen

        row = db.fetch_one("SELECT status FROM dsar_requests WHERE id = ?", (req_id,))
        # Cancelled mid-generation: must NOT be resurrected to 'ready'.
        assert row["status"] != "ready"
        assert deleted == [str(artifact)]


class TestM4ExpiredFileDeletion:
    def test_cleanup_expired_deletes_files(self, dsar_setup, tmp_path):
        from src.core.dsar.harvester import DSARHarvester

        db = dsar_setup
        f = tmp_path / "expired.json"
        f.write_text('{"pii": true}')
        req_id = int(time.time_ns() % 10**12) + 2
        past = int(time.time()) - 100
        db.execute(
            "INSERT INTO dsar_requests (id, user_id, status, requested_at,"
            " completed_at, expires_at, format, storage_backend, storage_path)"
            " VALUES (?, ?, 'ready', ?, ?, ?, 'json', 'local', ?)",
            (req_id, 42, past, past, past - 10, str(f)),
        )

        DSARHarvester(db)._cleanup_expired()

        row = db.fetch_one("SELECT status FROM dsar_requests WHERE id = ?", (req_id,))
        assert row["status"] == "expired"
        assert not f.exists()


class TestL5AuditChainReset:
    def test_broken_chain_reset_raises(self, tmp_path, monkeypatch):
        from src.core.dsar.audit_log import DSARLog

        log_file = tmp_path / "audit.jsonl"
        log_file.write_text('{"i": 1}\n{"i": 2}\n')

        log = DSARLog()
        monkeypatch.setattr(log, "file_path", str(log_file))
        monkeypatch.setattr(log, "_verify_chain_impl", lambda: (False, 2, "broken"))
        monkeypatch.setattr(
            "utils.config.get",
            lambda key, default=None: (
                {"rotate_on_broken_chain": True} if key == "dsar.audit_log" else default
            ),
        )

        with pytest.raises(RuntimeError):
            log.verify_chain()
