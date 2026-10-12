#!/usr/bin/env python3
"""Install the dependency scanners CI runs, at pinned versions and digests.

Both CI systems used to install these tools with a bare ``curl`` into a
``|| true`` step, and that is how the check rotted into a no-op without anyone
noticing:

* the osv-scanner download pointed at
  ``releases/latest/download/osv-scanner_linux_amd64.tar.gz``, which has not
  existed since v2 -- the release assets are raw binaries now -- so the fetch
  was a 404;
* the GitLab job runs in ``python:3.14.8-slim-trixie``, which ships neither
  ``curl`` nor ``wget``, so that fetch failed as "command not found" as well;
* the scans themselves were written ``./bin/osv-scanner . || true``, so even a
  successful install could not have failed the job;
* ``trivy fs`` exits 0 by default regardless of what it finds.

The result was a green "Dependency scanning" job that produced no
``osv-report.json`` and reported a trivy scan that was never compared against a
verdict. Installing from a pinned release with the digest checked makes the
step's own failure modes loud, and the scans are run without ``|| true`` -- see
the two CI configs that call this and the tests in
``src/tests/security/test_install_scanners.py``.

Usage:
    python ci-tooling/install_scanners.py --bin-dir ./bin osv-scanner
    python ci-tooling/install_scanners.py --bin-dir ./bin osv-scanner trivy

Only the standard library is used, so the GitLab job needs no extra image
packages: it downloads with ``urllib`` and verifies with ``hashlib``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

# Pinned on purpose. A moving ``latest`` URL is what let the old step break
# without the pipeline noticing; a version tag cannot drift under the build, and
# the digest is checked before anything is executed. Bump the version and the
# digest together, taking the digest from that release's own checksum file
# (osv-scanner_SHA256SUMS / trivy_<version>_checksums.txt), which is also what
# the tests in src/tests/security/test_install_scanners.py pin.
TOOLS = {
    "osv-scanner": {
        "version": "v2.7.0",
        "asset": "osv-scanner_linux_amd64",
        "url": (
            "https://github.com/google/osv-scanner/releases/download/{version}/{asset}"
        ),
        "sha256": "160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b",  # pragma: allowlist secret
        "binary": "osv-scanner",
    },
    "trivy": {
        "version": "v0.75.0",
        "asset": "trivy_0.75.0_Linux-64bit.tar.gz",
        "url": (
            "https://github.com/aquasecurity/trivy/releases/download/{version}/{asset}"
        ),
        "sha256": "c6e65abddb348e25f10549df887045629cf28cc72453cd1c63acb717316b3f3f",  # pragma: allowlist secret
        "binary": "trivy",
        # The release ships an archive; only this member is taken out of it.
        "member": "trivy",
    },
}


class InstallError(RuntimeError):
    """A scanner could not be installed and verified."""


def sha256_of(path: str) -> str:
    """The hex sha256 of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_scheme(url: str) -> None:
    """Refuse any transport but https, or a ``file://`` URL naming a real file.

    ``install_tool`` takes a URL override for the tests, which serve their
    fixture from disk; a ``file://`` URL that names nothing is a typo, and any
    other scheme (``http://``, ``ftp://``, a bare path) would let a caller point
    an install at a transport with no integrity on the wire. The digest is
    still checked after the download either way.

    Raises:
        InstallError: the URL is not https and is not an existing local file.
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "file":
        if os.path.isfile(urllib.request.url2pathname(parsed.path)):
            return
        raise InstallError(f"{url} names no file")
    raise InstallError(
        f"refusing to download from {parsed.scheme or 'a bare path'!r}: "
        "only https (or an existing file:// fixture) is accepted"
    )


def _download(url: str, dest: str) -> None:
    """Fetch ``url`` to ``dest``, failing on a non-2xx response."""
    _check_scheme(url)
    # The scheme was checked above; nosec B310 is the generic warning about
    # urlopen being handed an unvalidated URL.
    with urllib.request.urlopen(url, timeout=120) as response, open(dest, "wb") as out:  # nosec: B310
        shutil.copyfileobj(response, out)


def tool_url(tool: str) -> str:
    """The pinned release URL for a scanner."""
    spec = TOOLS[tool]
    return spec["url"].format(version=spec["version"], asset=spec["asset"])


def install_tool(tool: str, bin_dir: str, url: str | None = None) -> str:
    """Download, verify and install one scanner; return the installed path.

    The download lands in a temporary file next to the destination and is only
    moved into place once its digest matches, so a truncated download, a
    redirected URL or a tampered release leaves no runnable binary behind for
    the next step to execute.

    Args:
        tool: A key of ``TOOLS``.
        bin_dir: Directory to install into; created when missing.
        url: Override the pinned URL, for tests.

    Returns:
        The absolute path of the installed binary.

    Raises:
        InstallError: the tool is unknown, the download failed, or the bytes
            did not match the pinned digest.
    """
    spec = TOOLS.get(tool)
    if spec is None:
        raise InstallError(
            f"unknown scanner {tool!r}; known: {', '.join(sorted(TOOLS))}"
        )
    source = url or tool_url(tool)
    os.makedirs(bin_dir, exist_ok=True)
    target = os.path.join(bin_dir, spec["binary"])

    with tempfile.TemporaryDirectory(dir=bin_dir) as scratch:
        download = os.path.join(scratch, spec["asset"])
        try:
            _download(source, download)
        except Exception as exc:  # urllib raises a family of these
            raise InstallError(f"could not download {source}: {exc}") from exc

        actual = sha256_of(download)
        if actual != spec["sha256"]:
            raise InstallError(
                f"{tool} {spec['version']} digest mismatch for {source}\n"
                f"  expected: {spec['sha256']}\n"
                f"  actual:   {actual}\n"
                "Refusing to install it. If the release was re-rolled, update "
                "TOOLS in ci-tooling/install_scanners.py from that release's "
                "checksum file."
            )

        member = spec.get("member")
        if member:
            with tarfile.open(download, "r:*") as archive:
                try:
                    extracted = archive.extractfile(member)
                except KeyError:
                    raise InstallError(
                        f"{spec['asset']} does not contain {member!r}"
                    ) from None
                if extracted is None:
                    raise InstallError(
                        f"{spec['asset']} member {member!r} is not a file"
                    )
                payload = os.path.join(scratch, spec["binary"])
                with extracted, open(payload, "wb") as out:
                    shutil.copyfileobj(extracted, out)
        else:
            payload = download

        # 0o700: only the account running the job needs to execute it, and the
        # file has just been checked against its published digest.
        os.chmod(payload, 0o700)
        os.replace(payload, target)

    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the CI dependency scanners at pinned digests.",
    )
    parser.add_argument(
        "tools",
        nargs="+",
        choices=sorted(TOOLS),
        metavar="TOOL",
        help=f"one or more of: {', '.join(sorted(TOOLS))}",
    )
    parser.add_argument(
        "--bin-dir",
        default="./bin",
        help="where to install the binaries (default: ./bin)",
    )
    args = parser.parse_args(argv)

    for tool in args.tools:
        try:
            path = install_tool(tool, args.bin_dir)
        except InstallError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(
            f"Installed {tool} {TOOLS[tool]['version']} ({TOOLS[tool]['sha256'][:12]}) "
            f"to {path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
