#!/usr/bin/env python3
"""License audit for the Plexichat server's shipped dependencies.

Companion to the web client's ``npm run audit:licenses``
(``plexichat-client/scripts/license-audit.cjs``). The server runtime tree ships
no GPL/LGPL code either, so the two trees enforce the same rule; the exception
mechanism below is kept so that a future dependency needing legal review has a
place to be recorded rather than silently accepted:

* the shipping set is the pinned runtime tree in ``requirements.txt`` (test and
  developer tooling live in ``requirements-dev.txt`` and are not scanned),
* each distribution's license is resolved from PEP 639 ``License-Expression``,
  then the ``License ::`` trove classifiers, then the ``License`` field, and
  finally from the bundled ``LICENSE`` file when metadata is absent,
* a copyleft or unidentifiable license fails the audit unless it is listed in
  ``ALLOWED_COPYLEFT`` / ``ALLOWED_UNKNOWN`` below with a justification.

``MPL-2.0`` is treated as permissive (it is file-level copyleft with no link or
relink obligation); see ``PERMISSIVE``. The GPL family is not.

Failures are deliberate: a new LGPL/GPL dependency, or a package whose license
metadata cannot be resolved, must be a human decision rather than an accident of
a ``uv pip compile``.

Usage::

    python ci-tooling/license_audit.py                    # gate the runtime tree
    python ci-tooling/license_audit.py --format markdown   # regenerate the notices table
    python ci-tooling/license_audit.py --all               # audit everything installed

Exit codes: 0 = clean, 1 = unlisted copyleft/unknown license found, 2 = the
requirements lock could not be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# SPDX identifiers that are fine to ship without review.
PERMISSIVE = {
    "MIT",
    "MIT-0",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "0BSD",
    "BlueOak-1.0.0",
    "Unlicense",
    "CC0-1.0",
    "Zlib",
    "PSF-2.0",
    "Python-2.0",
    "MPL-2.0",  # weak copyleft, file-level only; reviewed and accepted
    "HPND",
    "MIT-CMU",  # MIT variant; Pillow's SPDX expression
}

# Legacy spellings found in ``License`` fields and trove classifiers.
NAME_MAP = {
    "mit": "MIT",
    "mit license": "MIT",
    "mit license (mit)": "MIT",
    "mit/x11": "MIT",
    "apache": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache 2.0 license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "3-clause bsd license": "BSD-3-Clause",
    "new bsd": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "2-clause bsd license": "BSD-2-Clause",
    "isc": "ISC",
    "isc license (iscl)": "ISC",
    "isc license": "ISC",
    "unlicense": "Unlicense",
    "the unlicense": "Unlicense",
    "cc0-1.0": "CC0-1.0",
    "zlib": "Zlib",
    "python-2.0": "Python-2.0",
    "python software foundation license": "PSF-2.0",
    "python software foundation license (psfl)": "PSF-2.0",
    "psfl": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mit no attribution": "MIT-0",
    "historical permission notice and disclaimer (hpnd)": "HPND",
    # Copyleft / unresolved spellings: kept distinct so the allowlist below
    # cannot be satisfied by a loose match.
    "lgpl": "LGPL",
    "lgpl-3.0-only": "LGPL-3.0-only",
    "lgpl-2.1-only": "LGPL-2.1-only",
    "lgpl with exceptions": "LGPL",
    "gnu lesser general public license v3 (lgplv3)": "LGPL-3.0-only",
    "gnu general public license (gpl)": "GPL",
    "gnu general public license v3 (gplv3)": "GPL-3.0-only",
    "gnu general public license v2 (gplv2)": "GPL-2.0-only",
    "gnu library or lesser general public license (lgpl)": "LGPL",
    "gnu affero general public license v3 (agplv3)": "AGPL-3.0-only",
}

COPYLEFT_PATTERN = re.compile(
    r"(agpl|lgpl|gpl|mpl|epl|cddl|sspl|cc-by-sa|cc-by-nc|busl|elastic|commons-clause)",
    re.IGNORECASE,
)

# --- Reviewed exceptions -----------------------------------------------------
#
# Copyleft or otherwise non-permissive licenses that are knowingly shipped, each
# with the reason it is acceptable and what would remove the entry. Anything not
# listed here fails the audit.
# Empty: every GPL/LGPL dependency has been removed from the runtime tree.
#
#   fpdf2        -> reportlab (BSD-3-Clause) for PDF export
#   odfpy        -> the ODT writer is hand-rolled in artifacts_export.py
#   psycopg*     -> pg8000 (BSD-3-Clause) for PostgreSQL
#
# Add an entry here only with legal sign-off; the notices file must be updated
# in the same change (see ``test_allowlisted_exceptions_are_documented``).
ALLOWED_COPYLEFT: Dict[str, str] = {}

# Distributions whose wheels carry no resolvable license metadata. Each has been
# verified upstream by hand; note that most of this list exists because the
# packaging tooling, not the license, is what is missing.
ALLOWED_UNKNOWN: Dict[str, str] = {
    # Empty today: the bundled-LICENSE fallback above resolves the packages whose
    # wheels carry no usable metadata (e.g. google-crc32c 1.8.0). Add an entry
    # here only after confirming the license by hand.
}

# Lock files. A distribution is treated as developer/test tooling when it is
# named in the dev lock but *not* in the runtime lock, so it is reported
# separately and never fails the audit: it is not part of the shipped image.
RUNTIME_REQUIREMENTS = REPO_ROOT / "requirements.txt"
DEV_REQUIREMENTS = REPO_ROOT / "requirements-dev.txt"


def canonical(name: str) -> str:
    """PEP 503 normalized distribution name."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def normalize(raw: str) -> Optional[str]:
    if not raw:
        return None
    value = re.sub(r"\s+", " ", str(raw)).strip()
    if not value:
        return None
    # Try the field verbatim first: keys such as
    # "mozilla public license 2.0 (mpl 2.0)" only match before the parentheses
    # are unwrapped.
    direct = NAME_MAP.get(value.lower())
    if direct:
        return direct
    unwrapped = re.sub(r"\(([^)]*)\)", r"\1", value)
    unwrapped = re.sub(r"\s+", " ", unwrapped).strip()
    return NAME_MAP.get(unwrapped.lower(), unwrapped)


def split_ids(expression: str) -> List[str]:
    """Split a combined SPDX expression ('MIT OR Apache-2.0') into ids."""
    parts = re.split(r"\s+(?:OR|AND|WITH)\s+|,", expression)
    ids = [normalize(part) for part in parts]
    return [i for i in ids if i]


def license_from_text(text: str) -> Optional[str]:
    """Identify a license from bundled license text."""
    head = text[:4000].lower()
    if "apache license" in head and "version 2.0" in head:
        return "Apache-2.0"
    if (
        "gnu lesser general public license" in head
        or "gnu library general public" in head
    ):
        return "LGPL"
    if "gnu affero general public license" in head:
        return "AGPL-3.0-only"
    if "gnu general public license" in head:
        return "GPL"
    if "mozilla public license" in head:
        return "MPL-2.0"
    if "redistribution and use in source and binary forms" in head:
        return "BSD-3-Clause"
    if "permission is hereby granted, free of charge" in head:
        return "MIT"
    if "permission to use, copy, modify, and/or distribute" in head:
        return "ISC"
    return None


def license_from_files(dist: metadata.Distribution) -> Optional[str]:
    """Look for a bundled LICENSE/COPYING file and identify it."""
    try:
        files = dist.files or []
    except Exception:
        return None
    for entry in files:
        name = Path(str(entry)).name
        if not re.match(r"^(LICENSE|LICENCE|COPYING)", name, re.IGNORECASE):
            continue
        text = ""
        try:
            text = dist.read_text(str(entry)) or ""
        except Exception:
            text = ""
        if not text:
            # ``read_text`` yields an empty string for some wheels (observed on
            # google-crc32c 1.8.0), so fall back to the filesystem path the
            # distribution reports.
            try:
                text = Path(dist.locate_file(entry)).read_text(
                    encoding="utf-8", errors="replace"
                )
            except Exception:
                continue
        found = license_from_text(text)
        if found:
            return found
    return None


def resolve_license(
    dist: metadata.Distribution,
) -> Tuple[List[str], Optional[str], str]:
    """Return (license ids, sniffed-from-file id, metadata source)."""
    meta = dist.metadata

    expression = meta.get("License-Expression")  # PEP 639
    if expression:
        # ``MIT-CMU``, ``LGPL-3.0-only``, ...
        return split_ids(expression), None, "License-Expression"

    # The deprecated ``License`` field is checked before the classifiers: some
    # wheels declare contradictory classifiers (qrcode 8.2 claims both
    # "BSD License" and "Other/Proprietary License") while the field itself is a
    # clean identifier.
    field_value = meta.get("License")
    if field_value:
        collapsed = re.sub(r"\s+", " ", field_value).strip()
        # A short field is an identifier; a long one is the license text itself.
        if (
            collapsed
            and len(collapsed) <= 128
            and collapsed.lower()
            not in (
                "unknown",
                "none",
                "",
            )
        ):
            # An exact match wins outright: "Apache License, Version 2.0" must
            # not be split on its comma into two unrecognised halves.
            mapped = NAME_MAP.get(collapsed.lower())
            if mapped:
                return [mapped], None, "License field"
            # Keep only values that are recognisable as an identifier. Free text
            # such as python-dateutil's "Dual License" is not informative, so it
            # falls through to the classifiers / bundled LICENSE file.
            ids = [
                i
                for i in split_ids(collapsed)
                if " " not in i
                or i in PERMISSIVE
                or i in ALLOWED_COPYLEFT
                or i in ALLOWED_UNKNOWN
            ]
            if ids:
                return ids, None, "License field"
        sniffed = license_from_text(collapsed)
        if sniffed:
            return [sniffed], sniffed, "License field (text)"

    classifiers = [
        c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")
    ]
    if classifiers:
        ids: List[str] = []
        for classifier in classifiers:
            # "License :: OSI Approved :: MIT License" -> "MIT License"
            leaf = classifier.split("::")[-1].strip()
            normalized = normalize(leaf)
            if normalized and normalized not in ids:
                ids.append(normalized)
        if ids:
            return ids, None, "classifier"

    sniffed = license_from_files(dist)
    if sniffed:
        return [sniffed], sniffed, "LICENSE file"

    return [], None, "-"


def classify(ids: List[str]) -> Optional[str]:
    """Return a failure reason, or None when every id is acceptable."""
    if not ids:
        return "no license metadata"
    problems = []
    for identifier in ids:
        if identifier in PERMISSIVE:
            continue
        if identifier in ALLOWED_COPYLEFT or identifier in ALLOWED_UNKNOWN:
            continue
        problems.append(
            f"copyleft ({identifier})"
            if COPYLEFT_PATTERN.search(identifier)
            else f"unknown ({identifier})"
        )
    return "; ".join(problems) if problems else None


@dataclass
class Result:
    name: str
    version: str
    ids: List[str] = field(default_factory=list)
    source: str = "-"
    reason: Optional[str] = None
    allowlisted: bool = False
    dev_only: bool = False
    installed: bool = True

    @property
    def key(self) -> str:
        """Bucket label used for the summary tally."""
        if not self.installed:
            return "NOT INSTALLED"
        if self.dev_only:
            return "DEV ONLY"
        if self.reason and not self.allowlisted:
            return "FAILED"
        if self.ids:
            return " OR ".join(self.ids)
        return "UNKNOWN"


def pinned_names(requirements: Path) -> List[str]:
    """Distribution names pinned in a ``requirements.txt`` style lock."""
    names: List[str] = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)", line)
        if match:
            names.append(match.group(1))
    return names


def dev_only_names() -> set:
    """Names that are dev-lock-only, i.e. tooling that never ships.

    The dev lock is a superset of the runtime lock (it starts with
    ``-r requirements.in``), so the runtime names must be subtracted; otherwise
    every shipped dependency would look like developer tooling.
    """
    dev = (
        {canonical(n) for n in pinned_names(DEV_REQUIREMENTS)}
        if DEV_REQUIREMENTS.is_file()
        else set()
    )
    runtime = (
        {canonical(n) for n in pinned_names(RUNTIME_REQUIREMENTS)}
        if RUNTIME_REQUIREMENTS.is_file()
        else set()
    )
    return dev - runtime


def audit(names: Optional[List[str]]) -> List[Result]:
    installed: Dict[str, metadata.Distribution] = {}
    for dist in metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            installed[canonical(name)] = dist

    if names is None:
        targets: List[Tuple[str, Optional[metadata.Distribution]]] = sorted(
            installed.items(), key=lambda item: item[0]
        )
    else:
        targets = [(canonical(n), installed.get(canonical(n))) for n in names]

    dev_only = dev_only_names()
    results: List[Result] = []
    for name, dist in targets:
        if dist is None:
            results.append(Result(name=name, version="-", installed=False))
            continue
        ids, _sniffed, source = resolve_license(dist)
        reason = classify(ids)
        is_dev = name in dev_only
        allowlisted = reason is not None and (
            is_dev or name in ALLOWED_COPYLEFT or name in ALLOWED_UNKNOWN
        )
        results.append(
            Result(
                name=name,
                version=dist.version,
                ids=ids,
                source=source,
                reason=reason,
                allowlisted=allowlisted,
                dev_only=is_dev,
            )
        )
    return results


def emit_text(results: List[Result], scope: str) -> None:
    tally: Dict[str, int] = {}
    for result in results:
        tally[result.key] = tally.get(result.key, 0) + 1

    print(f"Audited {len(results)} distributions ({scope}):")
    for license_id, count in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {license_id}: {count}")

    exceptions = [r for r in results if r.reason and r.allowlisted and not r.dev_only]
    if exceptions:
        print("\nReviewed exceptions (allowed, see ALLOWED_* in this script):")
        for result in exceptions:
            print(f"  - {result.name} {result.version}: {result.reason}")

    dev_only_failures = [r for r in results if r.reason and r.dev_only]
    if dev_only_failures:
        print(
            f"\nDev-only tooling with non-permissive/unknown licenses "
            f"({len(dev_only_failures)}, not shipped): "
            + ", ".join(r.name for r in dev_only_failures)
        )

    skipped = [r for r in results if not r.installed]
    if skipped:
        print(f"\nNot installed locally, skipped ({len(skipped)}):")
        print("  " + ", ".join(r.name for r in skipped))

    failures = [r for r in results if r.reason and not r.allowlisted]
    if failures:
        print(
            "\nLicense audit FAILED — unreviewed copyleft or unidentifiable licenses:"
        )
        for result in failures:
            print(
                f"  - {result.name} {result.version}: {result.reason} "
                f"(resolved via {result.source})"
            )
        print(
            f"\n{len(failures)} distribution(s) must be removed, replaced, or added "
            "to ALLOWED_COPYLEFT / ALLOWED_UNKNOWN with a justification."
        )
        raise SystemExit(1)

    print("\nAll shipped licenses are permissive or reviewed. OK")


def emit_markdown(results: List[Result], scope: str) -> None:
    tally: Dict[str, Dict[str, object]] = {}
    for result in results:
        entry = tally.setdefault(result.key, {"count": 0, "allowlisted": False})
        entry["count"] = int(entry["count"]) + 1
        if result.allowlisted and not result.dev_only:
            entry["allowlisted"] = True

    print("| License | Packages | Notes |")
    print("|---------|----------|-------|")
    for license_id, entry in sorted(
        tally.items(), key=lambda kv: (-int(kv[1]["count"]), kv[0])
    ):
        count = int(entry["count"])
        ids = [part.strip() for part in license_id.split(" OR ")]
        if license_id == "FAILED":
            note = "**Unreviewed — must be resolved.**"
        elif license_id == "DEV ONLY":
            note = "Developer/test tooling. Not shipped."
        elif license_id == "NOT INSTALLED":
            note = "In the lock but absent from this environment; skipped."
        elif entry["allowlisted"]:
            note = "Copyleft or unresolved metadata. Reviewed exception, see below."
        elif license_id == "UNKNOWN":
            note = "No metadata in the wheel; verified by hand, see below."
        elif any(COPYLEFT_PATTERN.search(i) for i in ids):
            note = "Weak copyleft, accepted (no linking obligation)."
        else:
            note = "Permissive."
        print(f"| {license_id} | {count} | {note} |")

    print("\n### Full listing\n")
    print("| Package | Version | License | Source |")
    print("|---------|---------|---------|--------|")
    for result in sorted(results, key=lambda r: r.name):
        if not result.installed:
            print(f"| `{result.name}` | - | not installed | - |")
            continue
        ids = " OR ".join(result.ids) if result.ids else "UNKNOWN"
        marker = (
            " (dev only)"
            if result.dev_only
            else (" (reviewed)" if result.allowlisted else "")
        )
        print(
            f"| `{result.name}` | {result.version} | {ids}{marker} | {result.source} |"
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--requirements",
        default=str(REPO_ROOT / "requirements.txt"),
        help="lock file defining the shipping set (default: requirements.txt)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="audit every installed distribution instead of the lock's contents",
    )
    parser.add_argument(
        "--format",
        choices=("text", "markdown", "json"),
        default="text",
        help="output format (markdown is used to regenerate THIRD_PARTY_NOTICES.md)",
    )
    args = parser.parse_args(argv)

    names: Optional[List[str]] = None
    scope = "all installed distributions"
    if not args.all:
        requirements = Path(args.requirements)
        if not requirements.is_file():
            print(
                f"FATAL: requirements lock not found: {requirements}", file=sys.stderr
            )
            return 2
        names = pinned_names(requirements)
        if not names:
            print(
                f"FATAL: no pinned distributions found in {requirements}",
                file=sys.stderr,
            )
            return 2
        scope = f"pinned runtime tree: {requirements.name}"

    results = audit(names)

    if args.format == "json":
        print(
            json.dumps(
                [
                    {
                        "name": r.name,
                        "version": r.version,
                        "licenses": r.ids,
                        "source": r.source,
                        "reason": r.reason,
                        "allowlisted": r.allowlisted,
                        "dev_only": r.dev_only,
                        "installed": r.installed,
                    }
                    for r in results
                ],
                indent=2,
                sort_keys=True,
            )
        )
    elif args.format == "markdown":
        emit_markdown(results, scope)
    else:
        emit_text(results, scope)

    failures = [r for r in results if r.reason and not r.allowlisted]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
