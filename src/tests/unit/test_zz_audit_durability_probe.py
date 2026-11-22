"""Probe: append-only telemetry audit log durability + concurrent append safety."""

from concurrent.futures import ThreadPoolExecutor

from src.core.telemetry import audit as audit_mod
from src.core.telemetry.audit import AuditChain


def _chain(tmp_path):
    # Explicit key sidecar-free keying so only append/head fsyncs are counted.
    return AuditChain(
        str(tmp_path / "chain.jsonl"),
        head_path=str(tmp_path / "chain.head"),
        chain_key="k" * 32,
        chain_key_id="probe",
    )


def test_each_append_fsyncs_data_and_head(tmp_path, monkeypatch):
    real_fsync = audit_mod.os.fsync
    calls = []

    def counting_fsync(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(audit_mod.os, "fsync", counting_fsync)

    chain = _chain(tmp_path)
    calls.clear()  # ignore __init__ (no sidecar key expected, but be safe)
    for i in range(3):
        chain.append("login", f"actor-{i}")

    # 1 data fsync + 1 head fsync per append => at least 6.
    assert len(calls) >= 6, f"only {len(calls)} fsync calls for 3 appends"
    lines = (tmp_path / "chain.jsonl").read_text().splitlines()
    assert len(lines) == 3
    ok, count, err = chain.verify_chain()
    assert ok and count == 3, (ok, count, err)


def test_concurrent_appends_do_not_corrupt_or_lose_entries(tmp_path):
    chain = _chain(tmp_path)
    n_threads, per_thread = 8, 10

    def worker(tid):
        for i in range(per_thread):
            chain.append("login", f"actor-{tid}-{i}")

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        list(pool.map(worker, range(n_threads)))

    ok, count, err = chain.verify_chain()
    assert ok, err
    assert count == n_threads * per_thread, (count, n_threads * per_thread)
    lines = (tmp_path / "chain.jsonl").read_text().splitlines()
    assert len(lines) == n_threads * per_thread
    seqs = sorted(__import__("json").loads(line)["entry"]["seq"] for line in lines)
    assert seqs == list(range(1, n_threads * per_thread + 1))
