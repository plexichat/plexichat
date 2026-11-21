"""Structural guard: every production decompression site must be bounded.

Follow-up to the decompression-bomb campaign: ``436f0a45`` hardened the static
client's ZIP extraction (metadata preflight, path/symlink checks, cleanup on
failure) and ``095efa15`` capped XLSX entry count and expansion ratio before
``openpyxl`` inflates the archive. Those are *behavioural* fixes to two files;
a third party can still add a new archive/image/stream decode anywhere in the
production tree with no cap at all, and no runtime test will notice.

This module is a complete, non-vacuous inventory of every production site that
decompresses or extracts data. It reads blobs from COMMITTED HEAD (not the
working tree -- several production files are contested by parallel agents and
a stale local copy must not decide the build), discovers sites by AST, and
fails if:

* a decompression call site appears in a file that is not registered below
  (a NEW site, or a novel name for one);
* an existing site gains a decompression API kind it is not registered for;
* a registered attacker-influenced site loses one of its size/count/ratio,
  path/symlink or cleanup-on-failure markers;
* an allowlisted site loses its written justification, or a bounded site
  stops being bounded.

The detector covers ``zipfile``/``tarfile`` extraction, ``gzip``/``bz2``/
``lzma``/``zlib``/``zstd`` decode, ``openpyxl.load_workbook`` and Pillow
``Image.open``. Non-vacuity is proven by feeding a synthetic *unbounded*
``zipfile.extractall`` through the very same checker: it is flagged, while the
bounded and documented variants are accepted.
"""

from __future__ import annotations

import ast
import subprocess
import textwrap
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run and pin every blob read to that commit."""
    sha = _git("rev-parse", "HEAD").strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _make_blob_reader(commit: str):
    def read(path: str) -> str:
        proc = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=_ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.returncode == 0, f"blob missing from {commit}: {path}"
        return proc.stdout

    return read


def _is_excluded(path: str) -> bool:
    """Tests and self-tests can legitimately build bombs; they are not prod."""
    return any(part in {"tests", "selftest"} for part in path.split("/")[:-1])


def _production_files(commit: str) -> tuple[str, ...]:
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit, "--", "src", "ci-tooling"],
        cwd=_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, "cannot list production trees from committed HEAD"
    files = tuple(
        p
        for p in proc.stdout.splitlines()
        if p.endswith(".py") and not _is_excluded(p)
    )
    assert files, "no production python modules found in the committed tree"
    return files


# ── detector ─────────────────────────────────────────────────────────────────


def _kind_of(call: ast.Call) -> str | None:
    """Return the decompression API kind of *call*, or ``None``.

    Only decode/extract entry points are counted. Compression (``zlib.compress``,
    ``ZstdCompressor``), ``crc32`` checksums and pure constructors are deliberately
    ignored so the inventory stays low-noise.
    """
    func = call.func
    if isinstance(func, ast.Attribute):
        recv, attr = ast.unparse(func.value), func.attr
        if recv == "zlib" and attr in {"decompress", "decompressobj"}:
            return f"zlib.{attr}"
        if recv == "gzip" and attr in {"decompress", "open", "GzipFile"}:
            return f"gzip.{attr}"
        if recv == "bz2" and attr in {"decompress", "open", "BZ2File"}:
            return f"bz2.{attr}"
        if recv == "lzma" and attr in {"decompress", "open", "LZMAFile"}:
            return f"lzma.{attr}"
        if recv in {"zstandard", "_zstd"} and attr in {
            "ZstdDecompressor",
            "stream_reader",
        }:
            return f"zstd.{attr}"
        if recv == "zipfile" and attr == "ZipFile":
            return "zipfile.ZipFile"
        if recv == "tarfile" and attr == "open":
            return "tarfile.open"
        if recv == "openpyxl" and attr == "load_workbook":
            return "openpyxl.load_workbook"
        if attr in {"extractall", "extract", "extractfile"}:
            # A raw archive extract sink. Flagged even without a ZipFile/tarfile
            # constructor so a novel wrapper cannot hide a new extraction site.
            return "archive.extract"
        if attr == "open" and (
            recv in {"Image", "self._Image", "PIL.Image"}
            or recv.endswith(".Image")
        ):
            return "PIL.Image.open"
    if isinstance(func, ast.Name) and func.id in {"load_workbook", "ZstdDecompressor"}:
        # ``from openpyxl import load_workbook`` / ``from zstandard import ...``.
        return f"bare.{func.id}"
    return None


def _decompression_kinds(tree: ast.AST) -> set[str]:
    kinds = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            kind = _kind_of(node)
            if kind:
                kinds.add(kind)
    return kinds


def _discover(files, reader) -> dict[str, set[str]]:
    """Map each production path that decompresses to its set of API kinds."""
    found: dict[str, set[str]] = {}
    for path in files:
        tree = ast.parse(reader(path), filename=path)
        kinds = _decompression_kinds(tree)
        if kinds:
            found[path] = kinds
    return found


# ── registry ─────────────────────────────────────────────────────────────────


class _Site(NamedTuple):
    path: str             # repo-relative path in committed HEAD
    kinds: frozenset[str] # decompression APIs expected at this site
    bounds: tuple[str, ...]  # substrings proving bounded decode; () => allowlisted
    reason: str           # why bounded, or why an allowlist is safe


# Attacker-influenced sites carry the actual guard markers. Allowlisted sites
# carry a written justification and no expected bounds. Every entry is one
# production file discovered by the detector above; keep this list exhaustive.
_SITES: dict[str, _Site] = {
    "static-client zip extraction": _Site(
        "src/static_client/fetcher.py",
        frozenset({"zipfile.ZipFile"}),
        (
            "_preflight_archive",      # validate all metadata before writing
            "_MAX_ARCHIVE_ENTRIES",    # entry-count cap
            "file_size",               # cumulative uncompressed byte cap
            "MAX_EXPANSION_RATIO",     # expansion-ratio cap
            "external_attr",           # symlink / special-file rejection
            "pre_existing",            # cleanup-on-failure of partial extraction
        ),
        "Untrusted static-asset ZIP: count/byte/ratio preflight, zip-slip and "
        "symlink validation, and cleanup of partial output (436f0a45).",
    ),
    "sum XLSX import": _Site(
        "src/core/suite/sum/manager_exchange.py",
        frozenset({"openpyxl.load_workbook", "zipfile.ZipFile"}),
        (
            "_reject_zip_bomb",
            "MAX_XLSX_ARCHIVE_ENTRIES",      # entry-count cap
            "MAX_XLSX_MEMBER_BYTES",         # per-member byte cap
            "MAX_XLSX_UNCOMPRESSED_BYTES",   # cumulative uncompressed byte cap
            "MAX_XLSX_EXPANSION_RATIO",      # expansion-ratio cap
        ),
        "Untrusted XLSX: central-directory caps checked before openpyxl inflates "
        "any member (095efa15).",
    ),
    "sum zstd container": _Site(
        "src/core/suite/sum/container.py",
        frozenset({"zstd.ZstdDecompressor"}),
        (
            "MAX_DECOMPRESSED_SIZE",
            "max_output_size",
            "total > max_output_size",  # abort mid-stream once ceiling is crossed
        ),
        "zstd workbook container is streamed under a hard output ceiling.",
    ),
    "gateway zlib stream": _Site(
        "src/api/websocket/compression.py",
        frozenset({"zlib.decompressobj"}),
        (
            "max_length=",                  # decompression output cap
            "unconsumed_tail",              # fail closed when the cap is hit
            "_clamp_decompression_limit",   # caller cannot raise the ceiling
            "_max_buffer",                  # cumulative compressed-input cap
        ),
        "Bounded WS frames: max_length cap, tail detection and a compressed "
        "buffer ceiling for fragmented streams.",
    ),
    "emoji image decode": _Site(
        "src/core/reactions/manager/emojis.py",
        frozenset({"PIL.Image.open"}),
        ("img.verify()", "max_emoji_size", "width_hint"),
        "Emoji upload: byte-size cap, verify() and a dimension gate before decode.",
    ),
    "avatar image decode": _Site(
        "src/core/avatars/manager/processing.py",
        frozenset({"PIL.Image.open"}),
        (
            "MAX_IMAGE_PIXELS",            # pixel cap
            "check_image_decode_limits",   # decoded-byte / ratio budget
            "declared_type_matches_bytes", # content sniffing
            "max_dim",                     # dimension cap
        ),
        "Avatar upload: pixel + dimension caps, decoded-byte/ratio budget and "
        "content-type sniffing before any load().",
    ),
    "media image compression": _Site(
        "src/core/media/compression.py",
        frozenset({"PIL.Image.open"}),
        ("_reject_decompression_bomb",),
        "Uploaded image: header pixel budget (all frames) checked before thumbnail "
        "forces a decode.",
    ),
    "perceptual hash decode": _Site(
        "src/core/media/phash.py",
        frozenset({"PIL.Image.open"}),
        ("_open_validated_image", "max_pixels"),
        "phash: header pixel cap enforced before imagehash forces a full decode.",
    ),
    "image processor": _Site(
        "src/core/media/processing/images.py",
        frozenset({"PIL.Image.open"}),
        (
            "_open_validated",
            "_validate_image_dimensions",
            "check_image_decode_limits",
            "MAX_IMAGE_PIXELS",
        ),
        "Central image processor: every decode entry point routes through "
        "dimension + decoded-budget validation.",
    ),
    "thumbnail skip probe": _Site(
        "src/core/media/_thumbnails.py",
        frozenset({"PIL.Image.open"}),
        (),
        "ALLOWLIST (internal-only, header-only probe): _should_skip_thumbnails "
        "reads img.width/img.height only and never calls load()/decode; real "
        "thumbnail generation goes through the bounded image processor.",
    ),
    "admin log reader": _Site(
        "src/core/admin/logs.py",
        frozenset({"zipfile.ZipFile", "zstd.ZstdDecompressor"}),
        (),
        "ALLOWLIST (admin-generated logs, internal-only): local operator log "
        "archives, not attacker-influenced; member name is checked with "
        "os.path.basename and streams are closed by _ArchiveStream.",
    ),
    "CI scanner installer": _Site(
        "ci-tooling/install_scanners.py",
        frozenset({"tarfile.open", "archive.extract"}),
        (),
        "ALLOWLIST (CI pinned downloads): sha256-digest-verified release asset, "
        "one named member taken via extractfile (never extractall); asserted by "
        "test_install_scanners.py.",
    ),
    "DSAR zip export": _Site(
        "src/core/dsar/export_formats.py",
        frozenset({"zipfile.ZipFile"}),
        (),
        "ALLOWLIST (internal-only export): ZipFile is opened in write mode to "
        "emit an archive; no untrusted data is decompressed.",
    ),
    "artifact ODF export": _Site(
        "src/api/routes/artifacts_export.py",
        frozenset({"zipfile.ZipFile"}),
        (),
        "ALLOWLIST (internal-only export): ZipFile is opened in write mode to "
        "emit an ODF container; no untrusted data is decompressed.",
    ),
}

_REGISTERED_PATHS = frozenset(site.path for site in _SITES.values())


def _site_violations(
    source: str,
    kinds: set[str],
    allowed_kinds: frozenset[str],
    bounds: tuple[str, ...],
    reason: str,
) -> list[str]:
    """Check one site against its registration; shared by guard and self-tests."""
    violations: list[str] = []
    if not kinds:
        violations.append("no decompression call found (stale registration)")
    novel = kinds - set(allowed_kinds)
    if novel:
        violations.append(f"novel/unregistered decompression API(s): {sorted(novel)}")
    if bounds:
        for marker in bounds:
            if marker not in source:
                violations.append(f"missing bound marker {marker!r}")
    else:
        # An allowlist is only valid with a written justification.
        if not reason.strip():
            violations.append("allowlisted site has no documented reason")
    if not reason.strip():
        violations.append("site has no reason")
    return violations


# ── guard tests ──────────────────────────────────────────────────────────────


def test_scan_is_not_vacuous(commit_sha):
    files = _production_files(commit_sha)
    assert len(files) >= 1000, f"only {len(files)} production modules scanned"
    assert _REGISTERED_PATHS <= set(files), "a registered site left the scanned tree"
    reader = _make_blob_reader(commit_sha)
    # The reader really returns committed content, not a stale working copy.
    assert "_preflight_archive" in reader("src/static_client/fetcher.py")
    assert "_reject_zip_bomb" in reader("src/core/suite/sum/manager_exchange.py")


def test_every_decompression_site_is_registered(commit_sha):
    """Fail closed on a new site or a novel decompression API (name)."""
    discovered = _discover(_production_files(commit_sha), _make_blob_reader(commit_sha))
    assert discovered, "no decompression sites found - the guard would pass vacuously"
    assert set(discovered) == set(_REGISTERED_PATHS), (
        "decompression-site inventory changed; register/unregister it in _SITES.\n"
        f"  unregistered (NEW sites): {sorted(set(discovered) - set(_REGISTERED_PATHS))}\n"
        f"  stale registry entries:    {sorted(set(_REGISTERED_PATHS) - set(discovered))}"
    )
    for path, kinds in discovered.items():
        site = next(s for s in _SITES.values() if s.path == path)
        novel = kinds - set(site.kinds)
        assert not novel, f"{path}: new decompression API not registered: {sorted(novel)}"


def test_registered_sites_keep_their_bounds(commit_sha):
    """Attacker-influenced sites keep their caps; allowlists keep their reasons."""
    reader = _make_blob_reader(commit_sha)
    checked_bounded = 0
    for name, site in _SITES.items():
        source = reader(site.path)
        kinds = _decompression_kinds(ast.parse(source, filename=site.path))
        violations = _site_violations(source, kinds, site.kinds, site.bounds, site.reason)
        assert not violations, f"{name} ({site.path}): " + "; ".join(violations)
        if site.bounds:
            checked_bounded += 1
    assert checked_bounded >= 6, "too few bounded sites - bounds guard is vacuous"
    assert len(_SITES) - checked_bounded >= 3, "allowlist path is not exercised"


# ── non-vacuity self-tests (same checker as the real guard) ──────────────────


def test_self_test_detector_recognises_every_documented_api():
    source = textwrap.dedent(
        """
        import zlib, gzip, bz2, lzma, zipfile, tarfile, openpyxl, zstandard
        from PIL import Image
        from openpyxl import load_workbook

        zlib.decompress(b"x")
        zlib.decompressobj()
        gzip.decompress(b"x")
        gzip.open("x", "rb")
        gzip.GzipFile(fileobj=None)
        bz2.decompress(b"x")
        bz2.open("x", "rb")
        lzma.decompress(b"x")
        lzma.open("x", "rb")
        zipfile.ZipFile("x")
        tarfile.open("x")
        archive.extractall("/tmp")
        archive.extractfile("x")
        openpyxl.load_workbook("x")
        load_workbook("x")
        zstandard.ZstdDecompressor()
        _zstd.ZstdDecompressor()
        Image.open(b"x")
        self._Image.open(b"x")
        """
    )
    found = _decompression_kinds(ast.parse(source))
    for expected in (
        "zlib.decompress",
        "zlib.decompressobj",
        "gzip.decompress",
        "gzip.open",
        "gzip.GzipFile",
        "bz2.decompress",
        "bz2.open",
        "lzma.decompress",
        "lzma.open",
        "zipfile.ZipFile",
        "tarfile.open",
        "archive.extract",
        "openpyxl.load_workbook",
        "bare.load_workbook",
        "zstd.ZstdDecompressor",
        "PIL.Image.open",
    ):
        assert expected in found, f"detector missed {expected!r}: {sorted(found)}"
    # Pure compression / checksum calls must not be reported as decode sites.
    clean = ast.parse(textwrap.dedent(
        """
        import zlib, zstandard
        zlib.compress(b"x")
        zlib.crc32(b"x")
        zstandard.ZstdCompressor().compress(b"x")
        """
    ))
    assert _decompression_kinds(clean) == set()


UNBOUNDED_ZIP = textwrap.dedent(
    """
    import zipfile

    def boom(path, dest):
        with zipfile.ZipFile(path) as zf:
            zf.extractall(dest)
    """
)
BOUNDED_ZIP = textwrap.dedent(
    """
    import os, zipfile
    MAX_ARCHIVE_ENTRIES = 20000
    MAX_EXPANSION_RATIO = 256

    def safe(path, dest):
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise ValueError("too many entries")
            total = sum(i.file_size for i in infos)
            compressed = sum(max(0, i.compress_size) for i in infos)
            if total // compressed > MAX_EXPANSION_RATIO:
                raise ValueError("bomb")
            for i in infos:
                if i.filename != os.path.basename(i.filename):
                    raise ValueError("unsafe path")
    """
)


def test_self_test_unbounded_extractall_flagged_bounded_and_documented_accepted():
    """The guard really distinguishes a bomb from a bounded/documented site."""
    unbounded_kinds = _decompression_kinds(ast.parse(UNBOUNDED_ZIP))
    assert unbounded_kinds == {"zipfile.ZipFile", "archive.extract"}

    required = ("MAX_ARCHIVE_ENTRIES", "file_size", "MAX_EXPANSION_RATIO", "basename")
    # Unbounded: flagged by the very checker used on production sites.
    violations = _site_violations(
        UNBOUNDED_ZIP, unbounded_kinds, frozenset(unbounded_kinds), required, "x"
    )
    assert any("MAX_ARCHIVE_ENTRIES" in v for v in violations), violations
    assert any("file_size" in v for v in violations), violations
    # Bounded: same checker, same required markers -> accepted.
    assert _site_violations(
        BOUNDED_ZIP, unbounded_kinds, frozenset(unbounded_kinds), required, "x"
    ) == []
    # Documented allowlist: no bounds required, but a reason is mandatory.
    assert _site_violations(
        UNBOUNDED_ZIP, unbounded_kinds, frozenset(unbounded_kinds), (),
        "CI pinned, digest-verified download",
    ) == []
    assert _site_violations(
        UNBOUNDED_ZIP, unbounded_kinds, frozenset(unbounded_kinds), (), ""
    )


def test_self_test_novel_site_and_novel_api_fail_closed():
    """A brand-new site and a new API in a known file are both caught."""
    # A new path absent from the registry is caught by the inventory equality.
    discovered = {"src/api/_new_upload_handler.py": {"zipfile.ZipFile"}}
    unregistered = set(discovered) - _REGISTERED_PATHS
    assert unregistered, "a novel site path must fail the inventory comparison"
    # A new API in an already-registered file is caught by the kind comparison.
    novel_src = "import gzip\ngzip.open('x', 'rb')\n"
    violations = _site_violations(
        novel_src,
        _decompression_kinds(ast.parse(novel_src)),
        frozenset({"PIL.Image.open"}),
        ("MAX_IMAGE_PIXELS",),
        "bounded site",
    )
    assert any("novel/unregistered decompression API" in v for v in violations)
