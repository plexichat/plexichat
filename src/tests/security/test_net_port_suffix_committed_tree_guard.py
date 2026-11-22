"""Narrow committed-tree guard for XFF port/bracket stripping in get_client_ip.

This is the committed-tree counterpart of
``test_trusted_resolver_port_suffix_spoof.py`` (N682 / 349287b6). That
behavioral test pins the *effect* of the stripping at runtime; this guard pins
the *code* in the committed blob, so a peer staging a whole stale copy of
``src/utils/net.py`` cannot silently regress it before the behavioral test ever
runs.

The strip block is security-critical: a trusted reverse proxy appends the
observed client as ``a.b.c.d:port`` or ``[IPv6]:port``. Without stripping, the
appended hop fails ``ipaddress`` parsing, the rightmost-untrusted-hop walk skips
it, and the attacker-controlled LEFTMOST XFF entry becomes the IP principal
used for rate limiting, IP bans and audit logs.

The reader uses ``git show <HEAD>:src/utils/net.py`` -- never the working tree
-- so it sees exactly what a peer would commit. HEAD is resolved once per run.

Non-vacuity: a synthetic stale blob with the strip block erased is flagged by
the exact same evaluator used for the real committed blob.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NET_PATH = "src/utils/net.py"
_PIN_KEY = "net-xff-port-suffix-strip"

# Distinctive, exact-whitespace pin of the hop-normalisation block: bracketed
# IPv6 ([v6]:port) unwrap AND single-colon IPv4:port suffix removal, immediately
# followed by the ipaddress validation that consumes the stripped ``host``.
PORT_SUFFIX_STRIP_MARKER = (
    '                    if host.startswith("[") and "]" in host:\n'
    '                        host = host[1 : host.index("]")]\n'
    '                    elif host.count(":") == 1:\n'
    '                        host = host.rsplit(":", 1)[0]\n'
    '                    try:\n'
    '                        ipaddress.ip_address(host)'
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
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


def _committed_net(commit: str) -> str:
    """Read the committed blob of net.py -- never the working copy."""
    proc = _git("show", f"{commit}:{_NET_PATH}")
    assert proc.returncode == 0, (
        f"cannot read committed {_NET_PATH}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _evaluate(blob: str) -> tuple[bool, str]:
    """Return ``(present, detail)`` for one candidate committed blob."""
    if PORT_SUFFIX_STRIP_MARKER in blob:
        return True, f"pin {_PIN_KEY!r} present in committed {_NET_PATH!r}"
    return False, (
        f"COMMITTED SECURITY FIX MARKER MISSING: {_PIN_KEY!r} -- the "
        "bracketed-IPv6 / IPv4:port stripping block is absent from the "
        f"committed blob {_NET_PATH!r}; a stale staged copy has regressed "
        "get_client_ip and reopens limiter-key spoofing via port-suffixed XFF "
        "hops"
    )


def test_committed_net_pins_port_suffix_strip(commit_sha: str) -> None:
    """The committed net.py still strips XFF port/bracket suffixes."""
    blob = _committed_net(commit_sha)
    present, detail = _evaluate(blob)
    assert present, detail
    assert blob.count(PORT_SUFFIX_STRIP_MARKER) == 1, (
        "strip block must appear exactly once in the committed blob"
    )


def test_committed_net_port_suffix_strip_non_vacuity() -> None:
    """Synthetic removal of the strip block is flagged by the same evaluator."""
    stale_blob = (
        "def get_client_ip(scope):\n"
        "    for candidate in reversed(parts):\n"
        "        host = candidate  # stale: no port/bracket stripping\n"
        "        try:\n"
        "            ipaddress.ip_address(host)\n"
        "        except ValueError:\n"
        "            continue\n"
    )
    assert PORT_SUFFIX_STRIP_MARKER not in stale_blob
    present, detail = _evaluate(stale_blob)
    assert not present, "synthetic stale copy without port stripping was not caught"
    assert _PIN_KEY in detail and "COMMITTED SECURITY FIX MARKER MISSING" in detail


def test_committed_net_marker_is_genuinely_distinctive(commit_sha: str) -> None:
    """The pin is not a ubiquitous string: a naive walk does not contain it."""
    naive_walk = (
        "for candidate in reversed(parts):\n"
        "    try:\n"
        "        ipaddress.ip_address(candidate)\n"
        "    except ValueError:\n"
        "        continue\n"
    )
    assert PORT_SUFFIX_STRIP_MARKER not in naive_walk
    assert PORT_SUFFIX_STRIP_MARKER in _committed_net(commit_sha)
