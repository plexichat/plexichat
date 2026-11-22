"""Throwaway: confirm the concurrency probe fails without the serializer."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from src.core.telemetry.audit import AuditChain


@contextmanager
def _no_exclusive(self):
    yield


def test_without_lock_chain_forks(tmp_path, monkeypatch):
    monkeypatch.setattr(AuditChain, "_exclusive", _no_exclusive)
    chain = AuditChain(
        str(tmp_path / "chain.jsonl"),
        head_path=str(tmp_path / "chain.head"),
        chain_key="k" * 32,
        chain_key_id="probe",
    )

    def worker(tid):
        for i in range(10):
            chain.append("login", f"actor-{tid}-{i}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))

    ok, count, err = chain.verify_chain()
    # With no serialization this MUST break (forked chain / duplicate seqs).
    assert not (ok and count == 80), f"lock removal not detected: {ok} {count} {err}"
