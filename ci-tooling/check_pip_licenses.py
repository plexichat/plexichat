#!/usr/bin/env python3
"""Gate ``pip-licenses`` output against the permissive-only shipping policy.

The in-tree audit (``ci-tooling/license_audit.py``) resolves each license from
installed metadata itself; this script is the independent second check that
runs over the ``pip-licenses`` tool's view of the same environment. The policy
is shared, not duplicated: normalization (``NAME_MAP``), the permissive set
(``PERMISSIVE``), and the reviewed exceptions (``ALLOWED_COPYLEFT`` /
``ALLOWED_UNKNOWN``) are all imported from ``license_audit`` so the two gates
cannot drift apart.

Scope is the pinned runtime tree in ``requirements.txt`` — anything installed
but not pinned there (``pip-audit``, ``safety``, ``pip-licenses`` itself, …)
is tooling, not shipped code, and is ignored. A pinned distribution absent
from the ``pip-licenses`` report is skipped with a warning, matching the
``NOT INSTALLED`` behaviour of ``license_audit.py``.

When the ``pip-licenses`` license string alone is unusable (empty field,
over-long composite notice, bare ``"Dual License"``), the checker re-resolves
that one distribution from its installed metadata with
``license_audit.resolve_license`` and accepts it if that is clean — unless
the reported string itself matches a copyleft pattern, which always fails.
A genuinely new GPL/LGPL dependency therefore fails both resolutions and is
caught; odd-but-benign spellings cannot redden the pipeline on their own.

Failures are deliberate and fail closed: a non-permissive or unresolvable
license must be removed, replaced, or explicitly allowlisted with legal
sign-off (and documented in ``THIRD_PARTY_NOTICES.md``).

Usage::

    pip-licenses --format=json --output-file=pip-licenses.json
    python ci-tooling/check_pip_licenses.py pip-licenses.json

Exit codes: 0 = clean, 1 = unlisted copyleft/unknown license found,
2 = usage error (unreadable report or lock file).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from importlib import metadata as _metadata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_license_audit():
    """Import ``ci-tooling/license_audit.py`` without polluting ``sys.path``."""
    tool_path = REPO_ROOT / "ci-tooling" / "license_audit.py"
    spec = importlib.util.spec_from_file_location("license_audit", tool_path)
    if spec is None or spec.loader is None:  # pragma: no cover - missing tool
        raise RuntimeError(f"cannot load license_audit from {tool_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("license_audit", module)
    spec.loader.exec_module(module)
    return module


la = _load_license_audit()


def load_report(path: Path) -> Dict[str, Dict[str, str]]:
    """Parse ``pip-licenses --format=json`` into ``{canonical-name: entry}``."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ValueError(f"{path} is not valid pip-licenses JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"{path} is not a pip-licenses JSON list")
    entries: Dict[str, Dict[str, str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name", "") or "")
        if not name:
            continue
        entries[la.canonical(name)] = {
            "name": name,
            "version": str(item.get("Version", "") or "-"),
            "license": str(item.get("License", "") or ""),
        }
    return entries


def license_ids(raw: str) -> List[str]:
    """Normalize one ``pip-licenses`` license string to SPDX-ish ids.

    Mirrors the ``License``-field handling in ``license_audit.resolve_license``
    exactly (verbatim match, then comma-split keeping only identifier-like
    parts, then license-text sniffing) so the two gates agree on odd
    spellings such as ``"Apache License, Version 2.0"``. Returns ``[]``
    when nothing usable can be extracted — a failure, not a pass.
    """
    if not raw:
        return []
    collapsed = re.sub(r"\s+", " ", str(raw)).strip()
    if not collapsed or collapsed.lower() in ("unknown", "none", ""):
        return []
    mapped = la.NAME_MAP.get(collapsed.lower())
    if mapped:
        return [mapped]
    if len(collapsed) <= 128:
        ids = [
            i
            for i in la.split_ids(collapsed)
            if " " not in i
            or i in la.PERMISSIVE
            or i in la.ALLOWED_COPYLEFT
            or i in la.ALLOWED_UNKNOWN
        ]
        if ids:
            return ids
    sniffed = la.license_from_text(collapsed)
    if sniffed:
        return [sniffed]
    return []


def installed_distributions() -> Dict[str, _metadata.Distribution]:
    """Currently installed distributions keyed by canonical name."""
    found: Dict[str, _metadata.Distribution] = {}
    for dist in _metadata.distributions():
        name = dist.metadata.get("Name")
        if name:
            found[la.canonical(name)] = dist
    return found


def resolve_local(
    dist: _metadata.Distribution,
) -> Tuple[List[str], Optional[str]]:
    """Re-resolve one installed distribution via ``license_audit``.

    Used only as a fallback when the ``pip-licenses`` string alone is
    unresolvable (empty field, over-long composite like reportlab's, or a
    bare ``"Dual License"``). Returns ``(ids, reason)``.
    """
    ids, _sniffed, _source = la.resolve_license(dist)
    return ids, la.classify(ids)


def check(report_path: Path, requirements_path: Path) -> int:
    """Run the gate. Returns a process exit code (0 / 1 / 2)."""
    if not requirements_path.is_file():
        print(
            f"FATAL: requirements lock not found: {requirements_path}",
            file=sys.stderr,
        )
        return 2
    try:
        entries = load_report(report_path)
    except FileNotFoundError:
        print(f"FATAL: pip-licenses report not found: {report_path}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2

    names = la.pinned_names(requirements_path)
    if not names:
        print(f"FATAL: no pinned distributions in {requirements_path}", file=sys.stderr)
        return 2

    installed = installed_distributions()
    failures: List[str] = []
    resolved_locally: List[str] = []
    skipped: List[str] = []
    checked = 0
    for name in names:
        key = la.canonical(name)
        entry = entries.get(key)
        if entry is None:
            skipped.append(name)
            continue
        checked += 1
        ids = license_ids(entry["license"])
        reason: Optional[str] = la.classify(ids)
        if reason is not None and (
            key in la.ALLOWED_COPYLEFT or key in la.ALLOWED_UNKNOWN
        ):
            continue
        if reason is None:
            continue
        # The pip-licenses string alone is unresolvable or non-permissive.
        # Anything smelling of copyleft fails immediately and is never
        # masked by the fallback below.
        if la.COPYLEFT_PATTERN.search(entry["license"]):
            failures.append(
                f"  - {entry['name']} {entry['version']}: {reason} "
                f"(pip-licenses reported {entry['license']!r})"
            )
            continue
        dist = installed.get(key)
        local_ids, local_reason = (
            resolve_local(dist) if dist is not None else ([], "not installed")
        )
        if local_reason is None:
            # Benign odd spelling (empty field, composite notice, ...): the
            # installed metadata itself resolves cleanly.
            resolved_locally.append(f"{entry['name']} ({' OR '.join(local_ids)})")
            continue
        failures.append(
            f"  - {entry['name']} {entry['version']}: {reason} "
            f"(pip-licenses reported {entry['license']!r}; "
            f"local metadata resolves to "
            f"{' OR '.join(local_ids) if local_ids else 'nothing usable'})"
        )

    print(f"Checked {checked} pinned distributions against pip-licenses output.")
    if resolved_locally:
        print(
            f"Accepted via installed metadata "
            f"({len(resolved_locally)}, pip-licenses string unusable):"
        )
        print("  " + ", ".join(sorted(resolved_locally)))

    if skipped:
        print(f"Not in pip-licenses output, skipped ({len(skipped)}):")
        print("  " + ", ".join(skipped))
    if failures:
        print("\npip-licenses gate FAILED — non-permissive or unresolvable licenses:")
        for line in failures:
            print(line)
        print(
            f"\n{len(failures)} distribution(s) must be removed, replaced, or added "
            "to ALLOWED_COPYLEFT / ALLOWED_UNKNOWN in ci-tooling/license_audit.py "
            "with a justification (and documented in THIRD_PARTY_NOTICES.md)."
        )
        return 1
    print("All shipped licenses are permissive or reviewed. OK")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report", help="pip-licenses --format=json output file")
    parser.add_argument(
        "--requirements",
        default=str(REPO_ROOT / "requirements.txt"),
        help="lock file defining the shipping set (default: requirements.txt)",
    )
    args = parser.parse_args(argv)
    return check(Path(args.report), Path(args.requirements))


if __name__ == "__main__":
    sys.exit(main())
