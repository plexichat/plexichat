#!/usr/bin/env python3
"""Watch Debian for a fixed libbrotli1 and say when to cut the release.

CVE-2026-111497 (the Brotli decoder out-of-bounds write) is fixed in Brotli
1.2.1. The backend image is built on ``python:3.14.8-slim-trixie``, whose
``libbrotli1`` is still 1.1.0-2 -- inside the vulnerable range -- and Debian
trixie has published no newer version, so there is nothing to upgrade to and
nothing to pin. Nothing in the application decompresses attacker-controlled
Brotli (no ``brotli`` package, no ``br`` request handling; only the localhost
healthcheck curl and ffprobe's header parse ever touch the library), which is
why shipping it is acceptable in the meantime.

That acceptance expires the day Debian ships a fixed package. This script is
the tripwire for it: it asks sources.debian.org what trixie has, compares
against the version we knowingly ship, and exits 10 -- with the rebuild-and-
release instructions -- once a newer one appears. The CI job that runs it is
``allow_failure: true`` so the signal is visible without blocking unrelated
work.

Usage:
    python ci-tooling/check_libbrotli.py            # query Debian
    python ci-tooling/check_libbrotli.py --json-file listing.json   # offline

Exit codes: 0 nothing newer, 10 a fixed libbrotli1 is available, 2 the lookup
itself failed (network, bad JSON) -- which is not the same thing as "still
vulnerable" and must not be reported as clean.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from functools import cmp_to_key
from itertools import zip_longest
from urllib.parse import urlparse

SOURCES_URL = "https://sources.debian.org/api/src/brotli/"

# The suite the backend image is built from (python:3.14.8-slim-trixie) and the
# libbrotli1 version it resolves to, which we knowingly ship.
SUITE = "trixie"
ACCEPTED_VERSION = "1.1.0-2"


# Token ranks, in the order dpkg compares them: a tilde sorts before the end of
# the string, the end of the string before letters, and letters before digits.
_TILDE = (0, "")
_END = (1, "")
_LETTERS = 2
_DIGITS = 3


def _tokens(part: str) -> list[tuple]:
    """``"1.2.0~rc1"`` -> ``[(3, 1), (3, 2), (3, 0), (0, ""), (2, "rc"), (3, 1)]``."""
    out: list[tuple] = []
    for chunk in re.findall(r"\d+|[a-zA-Z]+|~", part):
        if chunk == "~":
            out.append(_TILDE)
        elif chunk.isdigit():
            out.append((_DIGITS, int(chunk)))
        else:
            out.append((_LETTERS, chunk))
    return out


def _compare_tokens(left: list[tuple], right: list[tuple]) -> int:
    for first, second in zip_longest(left, right, fillvalue=_END):
        if first != second:
            return -1 if first < second else 1
    return 0


def compare_versions(left: str, right: str) -> int:
    """Compare two Debian versions the way ``dpkg --compare-versions`` does.

    Only the pieces that matter for "has Debian shipped anything newer than
    what we ship" are modelled -- epoch, upstream, revision, the ``~`` that
    sorts before the empty string, and numeric (not lexicographic) runs, which
    is what stops 1.10.0 from ranking below 1.2.0.
    """

    def split(version: str) -> tuple[int, list[tuple], list[tuple]]:
        epoch = 0
        if ":" in version:
            epoch_text, version = version.split(":", 1)
            epoch = int(epoch_text)
        if "-" in version:
            upstream, revision = version.rsplit("-", 1)
        else:
            upstream, revision = version, "0"
        return epoch, _tokens(upstream), _tokens(revision)

    left_epoch, left_upstream, left_revision = split(left)
    right_epoch, right_upstream, right_revision = split(right)
    if left_epoch != right_epoch:
        return -1 if left_epoch < right_epoch else 1
    upstream_cmp = _compare_tokens(left_upstream, right_upstream)
    if upstream_cmp != 0:
        return upstream_cmp
    return _compare_tokens(left_revision, right_revision)


def newest_in_suite(payload: dict, suite: str = SUITE) -> str | None:
    """The newest version sources.debian.org lists for ``suite``, or None."""
    versions = [
        entry["version"]
        for entry in payload.get("versions", [])
        if suite in entry.get("suites", [])
    ]
    if not versions:
        return None
    return max(versions, key=cmp_to_key(compare_versions))


def evaluate(
    payload: dict, suite: str = SUITE, accepted: str = ACCEPTED_VERSION
) -> tuple:
    """Return ``(newest, action_needed)`` for a sources.debian.org payload."""
    newest = newest_in_suite(payload, suite)
    if newest is None:
        raise ValueError(f"no version of the brotli source package in suite '{suite}'")
    return newest, compare_versions(newest, accepted) > 0


def fetch(url: str = SOURCES_URL, timeout: float = 20.0) -> dict:
    """Fetch the sources.debian.org listing.

    Only https is accepted. In production the URL is the constant above; the
    parameter exists so a test can point it at a local stand-in, and anything
    else reaching this function is a bug rather than a download.
    """
    if urlparse(url).scheme != "https":
        raise ValueError(f"refusing to fetch a non-https URL: {url}")
    with urllib.request.urlopen(url, timeout=timeout) as response:  # nosec B310
        return json.load(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--suite", default=SUITE, help=f"Debian suite to watch (default: {SUITE})"
    )
    parser.add_argument(
        "--accepted",
        default=ACCEPTED_VERSION,
        help=f"libbrotli1 version we knowingly ship (default: {ACCEPTED_VERSION})",
    )
    parser.add_argument(
        "--json-file",
        help="read a saved sources.debian.org response instead of querying the network",
    )
    args = parser.parse_args(argv)

    try:
        if args.json_file:
            with open(args.json_file, encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            payload = fetch()
        newest, action_needed = evaluate(payload, args.suite, args.accepted)
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"libbrotli-watch: lookup failed: {exc}", file=sys.stderr)
        return 2

    if action_needed:
        print(
            f"libbrotli-watch: Debian {args.suite} now has libbrotli {newest} "
            f"(shipping {args.accepted}).",
        )
        print(
            "CVE-2026-111497 is fixed in Brotli 1.2.1, so this release can move: "
            "rebuild the backend image on the new base, confirm "
            "`dpkg -l libbrotli1` in the image, then cut the next a.1.0 release.",
        )
        return 10

    print(
        f"libbrotli-watch: Debian {args.suite} is still at libbrotli {newest}; "
        f"nothing newer than the {args.accepted} we ship.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
