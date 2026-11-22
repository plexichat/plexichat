"""N694 follow-up: committed-tree pin for get_client_ip's fail-closed default.

``get_client_ip`` (``src/utils/net.py``) decides whether to trust forwarded
headers at all from one bootstrap value: the ``trusted_proxies`` list. It starts
as an empty list, is read from config with an empty-list fallback, and the
except branch also collapses to an empty list. An empty list makes
``_ip_matches_trusted(direct_ip, trusted_proxies)`` false for every peer, so the
whole XFF/CF trust block is skipped and the socket peer stays the principal.
That is the root of the trust boundary: if this default ever becomes permissive
(``["0.0.0.0/0"]``, ``["*"]``, or an unguarded ``None`` -> wildcard), ANY client
can pick its own address through X-Forwarded-For / CF-Connecting-IP and own the
rate-limit bucket, IP ban lookup and audit principal.

The rest of ``get_client_ip`` is security-critical too, but the existing
committed-tree guards only cover part of it:

* ``test_net_port_suffix_committed_tree_guard.py`` pins the bracketed-IPv6 /
  IPv4:port strip block;
* ``test_ip_canonicalization_structural_guard.py`` pins the IP canonicalizers
  (including ``normalise_ip_address``) used downstream of the resolver;
* the trusted-peer gate, the rightmost-untrusted-hop ``reversed(parts)`` walk
  and the fail-closed ``trusted_proxies`` default are covered only by RUNTIME
  tests (``test_trusted_resolver_*``), which a peer staging a whole stale copy
  of ``net.py`` can regress before any of them run.

This guard pins the one invariant that gates all the others: the fail-closed
``trusted_proxies`` default. It reads HEAD blobs only (``git show <HEAD>:...``,
never the working tree), and its non-vacuity is proven with a synthetic blob
that erases the fail-closed default and another that replaces it with a
trust-all wildcard -- both are flagged by the exact same evaluator.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NET_PATH = "src/utils/net.py"
_PIN_KEY = "net-trusted-proxies-fail-closed-default"

# Exact-whitespace pin of the trusted_proxies bootstrap block in
# get_client_ip: empty initial value, empty-list config default via ``or []``,
# and an empty-list fallback in the except branch. Any of the three collapsing
# to a permissive value removes this marker and is flagged below.
FAIL_CLOSED_DEFAULT_MARKER = (
    "    trusted_proxies: List[str] = []\n"
    "    try:\n"
    '        api_conf = config.get("api", {})\n'
    '        trusted_proxies = api_conf.get("trusted_proxies", []) or []\n'
    "    except Exception:\n"
    "        trusted_proxies = []"
)


def _git(*args: str, cwd: Path = _REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run, then pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _make_net_reader(commit: str, repo_root: Path = _REPO_ROOT):
    """Return a reader that fetches ``<commit>:<path>`` -- never the worktree."""

    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}", cwd=repo_root)
        assert proc.returncode == 0, (
            f"cannot read committed {path}: {proc.stderr.strip()}"
        )
        return proc.stdout

    return read


def _evaluate(blob: str) -> tuple[bool, str]:
    """Return ``(present, detail)`` for one candidate committed blob."""
    if FAIL_CLOSED_DEFAULT_MARKER in blob:
        return True, f"pin {_PIN_KEY!r} present in committed {_NET_PATH!r}"
    return False, (
        f"COMMITTED SECURITY FIX MARKER MISSING: {_PIN_KEY!r} -- the "
        "fail-closed trusted_proxies default is absent from the committed blob "
        f"{_NET_PATH!r}; a stale staged copy has regressed get_client_ip and "
        "lets any client choose its own rate-limit / ban / audit IP principal "
        "through X-Forwarded-For or CF-Connecting-IP"
    )


def test_committed_net_pins_fail_closed_trusted_proxies_default(
    commit_sha: str,
) -> None:
    """The committed net.py still defaults trusted_proxies to an empty list."""
    blob = _make_net_reader(commit_sha)(_NET_PATH)
    present, detail = _evaluate(blob)
    assert present, detail
    assert blob.count(FAIL_CLOSED_DEFAULT_MARKER) == 1, (
        "fail-closed default block must appear exactly once in the committed blob"
    )


def test_non_vacuity_default_block_removed_is_flagged() -> None:
    """A synthetic blob with the fail-closed bootstrap erased is flagged."""
    stale_blob = (
        "def get_client_ip(scope):\n"
        "    trusted_proxies = config.get('api', {}).get('trusted_proxies')\n"
        "    if any(_ip_matches_trusted(p, trusted_proxies) for p in parts):\n"
        "        return parts[0]\n"
    )
    assert FAIL_CLOSED_DEFAULT_MARKER not in stale_blob
    present, detail = _evaluate(stale_blob)
    assert not present, "synthetic blob without the fail-closed default was not caught"
    assert _PIN_KEY in detail and "COMMITTED SECURITY FIX MARKER MISSING" in detail


def test_non_vacuity_trust_all_default_is_flagged() -> None:
    """A synthetic wildcard default (the trust-all regression) is flagged."""
    trust_all_blob = (
        "def get_client_ip(scope):\n"
        "    trusted_proxies: List[str] = ['0.0.0.0/0']\n"
        "    try:\n"
        "        api_conf = config.get('api', {})\n"
        "        trusted_proxies = api_conf.get('trusted_proxies', []) or ['0.0.0.0/0']\n"
        "    except Exception:\n"
        "        trusted_proxies = ['0.0.0.0/0']\n"
    )
    assert FAIL_CLOSED_DEFAULT_MARKER not in trust_all_blob
    present, detail = _evaluate(trust_all_blob)
    assert not present, "synthetic trust-all default was not caught"
    assert _PIN_KEY in detail and "COMMITTED SECURITY FIX MARKER MISSING" in detail


def test_marker_is_genuinely_distinctive(commit_sha: str) -> None:
    """The pin is not a ubiquitous string: a naive default does not contain it."""
    naive_default = (
        "def get_client_ip(scope):\n"
        "    trusted_proxies = []\n"
        "    if direct_ip and _ip_matches_trusted(direct_ip, trusted_proxies):\n"
        "        ...\n"
    )
    assert FAIL_CLOSED_DEFAULT_MARKER not in naive_default
    assert FAIL_CLOSED_DEFAULT_MARKER in _make_net_reader(commit_sha)(_NET_PATH)


def test_reader_reads_committed_blob_not_working_tree(tmp_path: Path) -> None:
    """Non-vacuity: the reader really returns HEAD, not a stale worktree copy."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / _NET_PATH
    target.parent.mkdir(parents=True)
    target.write_text("MARKER\n" + FAIL_CLOSED_DEFAULT_MARKER + "\n", encoding="utf-8")

    assert _git("init", "-q", cwd=repo).returncode == 0
    assert _git("add", _NET_PATH, cwd=repo).returncode == 0
    committed = _git(
        "-c",
        "user.email=guard@example.invalid",
        "-c",
        "user.name=guard",
        "commit",
        "-qm",
        "fix",
        cwd=repo,
    )
    assert committed.returncode == 0, committed.stderr
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    assert sha

    # Peer-style stale working copy: the fail-closed default is gone on disk.
    target.write_text("MARKER_REMOVED\n", encoding="utf-8")
    assert FAIL_CLOSED_DEFAULT_MARKER not in target.read_text(encoding="utf-8")

    reader = _make_net_reader(sha, repo_root=repo)
    assert FAIL_CLOSED_DEFAULT_MARKER in reader(_NET_PATH)
