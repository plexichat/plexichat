"""Coalesced, credential/content-scoped video metadata resolution.

Before this change every ``get_metadata_from_bytes`` call spawned a fresh
``ffprobe`` subprocess, even for identical bytes resolved seconds apart or by
concurrent requests. That made the metadata endpoint an easy CPU/process
amplification target and wasted work on repeat reads of the same attachment.

These tests pin the three properties that make the memo safe to use:

* repeated identical work (sequential and concurrent) is computed once;
* the key carries the full scope + complete content hash, so a result from
  one credential/scope or one file is never served to another;
* mutation / TTL / explicit invalidation force a fresh probe, failures are
  never memoized, and the in-flight machinery stays bounded.
"""

import threading
import time

import pytest

from src.core.media.exceptions import VideoProcessingError
from src.core.media.processing import video
from src.core.media.processing._metadata_cache import MetadataResultCache


# A minimal, valid ffprobe response so the real parser is exercised.
def _ffprobe_json(width: int) -> bytes:
    import json

    return json.dumps(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": width,
                    "height": 720,
                    "duration": "1.0",
                    "r_frame_rate": "30/1",
                    "nb_frames": "30",
                }
            ],
            "format": {"duration": "1.0", "bit_rate": "1000"},
        }
    ).encode()


@pytest.fixture
def _isolated_video_config(monkeypatch):
    """Keep ``VideoProcessor`` construction off the real app config."""
    media_config = {"video_processing": {}}

    def fake_config_get(key, default=None):
        return media_config if key == "media" else default

    monkeypatch.setattr(video.config, "get", fake_config_get)


class _Probe:
    """Counting ffprobe double whose reported width can change on demand."""

    def __init__(self, monkeypatch, width: int = 1920):
        self.calls = []
        self.width = width
        self.fail = False
        self.gate = None

        def fake_run(*args, **kwargs):
            self.calls.append(kwargs.get("input") or args)
            if self.gate is not None:
                self.gate.wait(5)
            if self.fail:
                return type(
                    "R",
                    (),
                    {"returncode": 1, "stdout": b"", "stderr": b"ffprobe boom"},
                )()
            return type(
                "R",
                (),
                {"returncode": 0, "stdout": _ffprobe_json(self.width), "stderr": b""},
            )()

        monkeypatch.setattr(video.subprocess, "run", fake_run)


def _processor(monkeypatch, _isolated_video_config, ffprobe_path="/usr/bin/ffprobe"):
    proc = video.VideoProcessor()
    proc._ffprobe_path = ffprobe_path
    return proc


@pytest.mark.media
class TestVideoMetadataDedup:
    def test_sequential_identical_calls_probe_once(
        self, monkeypatch, _isolated_video_config
    ):
        probe = _Probe(monkeypatch)
        proc = _processor(monkeypatch, _isolated_video_config)
        blob = b"video-payload" * 32

        results = [proc.get_metadata_from_bytes(blob) for _ in range(20)]

        assert len(probe.calls) == 1, "20 identical reads must spawn one ffprobe"
        assert all(r.width == 1920 for r in results)
        stats = proc._metadata_cache.stats()
        assert stats["computes"] == 1
        assert stats["hits"] == 19

    def test_concurrent_identical_calls_coalesce(
        self, monkeypatch, _isolated_video_config
    ):
        probe = _Probe(monkeypatch)
        probe.gate = threading.Event()
        proc = _processor(monkeypatch, _isolated_video_config)
        blob = b"concurrent-payload" * 32
        results = []

        def worker():
            results.append(proc.get_metadata_from_bytes(blob))

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        # Hold the leader's probe open until every follower has joined the
        # in-flight key. Reaching the coalesced count proves no duplicate
        # probe was started for those followers.
        deadline = time.monotonic() + 5
        while proc._metadata_cache.stats()["coalesced"] < 11:
            assert time.monotonic() < deadline, "followers did not coalesce"
            time.sleep(0.005)
        probe.gate.set()
        for t in threads:
            t.join(5)

        assert len(probe.calls) == 1, "12 in-flight identical reads must coalesce"
        assert len(results) == 12
        assert all(r.width == 1920 for r in results)
        assert proc._metadata_cache.stats()["coalesced"] == 11
        assert proc._metadata_cache.stats()["inflight"] == 0

    def test_scope_and_content_are_partitioned_in_the_key(self):
        digest = "a" * 64
        other = "b" * 64
        assert MetadataResultCache.make_key(
            "cred-A", digest
        ) == MetadataResultCache.make_key("cred-A", digest)
        assert MetadataResultCache.make_key(
            "cred-A", digest
        ) != MetadataResultCache.make_key("cred-B", digest), (
            "a different credential/scope must never share an entry"
        )
        assert MetadataResultCache.make_key(
            "cred-A", digest
        ) != MetadataResultCache.make_key("cred-A", other), (
            "different file content must never share an entry"
        )

    def test_scope_isolation_no_cross_credential_result_reuse(
        self, monkeypatch, _isolated_video_config
    ):
        """A cached result under scope A is never served to scope B."""
        probe = _Probe(monkeypatch, width=100)
        proc_a = _processor(monkeypatch, _isolated_video_config, "/usr/bin/ffprobeA")
        proc_b = _processor(monkeypatch, _isolated_video_config, "/usr/bin/ffprobeB")
        blob = b"same-bytes-different-scope" * 16

        first = proc_a.get_metadata_from_bytes(blob)
        # Change the probe output: a scope-B call must run its own probe rather
        # than return scope A's cached width of 100.
        probe.width = 999
        second = proc_b.get_metadata_from_bytes(blob)

        assert first.width == 100
        assert second.width == 999, "scope B reused scope A's cached metadata"
        assert len(probe.calls) == 2

        # Re-reading scope A still returns its own cached value without a new
        # probe even though the probe's output has since changed.
        assert proc_a.get_metadata_from_bytes(blob).width == 100
        assert len(probe.calls) == 2

    def test_different_content_is_not_shared(self, monkeypatch, _isolated_video_config):
        probe = _Probe(monkeypatch, width=111)
        proc = _processor(monkeypatch, _isolated_video_config)

        proc.get_metadata_from_bytes(b"file-one")
        probe.width = 222
        result = proc.get_metadata_from_bytes(b"file-two")

        assert result.width == 222
        assert len(probe.calls) == 2

    def test_explicit_clear_forces_reprobe(self, monkeypatch, _isolated_video_config):
        probe = _Probe(monkeypatch)
        proc = _processor(monkeypatch, _isolated_video_config)
        blob = b"clear-me"

        proc.get_metadata_from_bytes(blob)
        proc.clear_metadata_cache()
        proc.get_metadata_from_bytes(blob)

        assert len(probe.calls) == 2, "mutation must drop stale metadata"

    def test_ttl_expiry_forces_reprobe(self, monkeypatch, _isolated_video_config):
        probe = _Probe(monkeypatch)
        proc = _processor(monkeypatch, _isolated_video_config)
        proc._metadata_cache = MetadataResultCache(ttl_seconds=0.05)
        blob = b"short-lived"

        proc.get_metadata_from_bytes(blob)
        proc.get_metadata_from_bytes(blob)
        assert len(probe.calls) == 1
        time.sleep(0.08)
        proc.get_metadata_from_bytes(blob)

        assert len(probe.calls) == 2, "expired entries must be recomputed"

    def test_failures_are_not_memoized(self, monkeypatch, _isolated_video_config):
        probe = _Probe(monkeypatch)
        proc = _processor(monkeypatch, _isolated_video_config)
        probe.fail = True

        for _ in range(3):
            with pytest.raises(VideoProcessingError):
                proc.get_metadata_from_bytes(b"failing-bytes")

        assert len(probe.calls) == 3, "a transient probe failure must be retryable"
        assert proc._metadata_cache.stats()["size"] == 0

    def test_inflight_is_bounded(self):
        cache = MetadataResultCache(maxsize=4, ttl_seconds=30, max_inflight=1)
        gate = threading.Event()
        entered = threading.Event()
        results = []

        def blocked_compute():
            entered.set()
            gate.wait(2)
            return "value"

        leader = threading.Thread(
            target=lambda: results.append(cache.get_or_compute("k", blocked_compute))
        )
        leader.start()
        assert entered.wait(2)
        # Second caller for the same key coalesces; callers for other keys are
        # rejected rather than pinning unbounded waiter state.
        follower = threading.Thread(
            target=lambda: results.append(cache.get_or_compute("k", lambda: "never"))
        )
        overflow = threading.Thread(
            target=lambda: results.append(
                cache.get_or_compute("other", lambda: "fallback")
            )
        )
        follower.start()
        overflow.start()
        gate.set()
        for t in (leader, follower, overflow):
            t.join(2)

        assert results.count("value") >= 1
        assert "fallback" in results
        assert cache.stats()["inflight"] == 0
