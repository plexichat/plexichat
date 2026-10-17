#!/usr/bin/env python3
"""Write the canonical provisional test license to disk for CI self-tests.

The server gates premium features (PlexiJoin federation, artifacts, …) behind
a signed license. CI runners have no commercial license, so the licensed-only
endpoints would otherwise be skipped and never exercised. This helper writes
the project's canonical provisional license — ``type: "test"``, unsigned, and
restricted to localhost by ``DEFAULT_TEST_ALLOWED_HOSTS`` — to the default
license location so ``python main.py self-test`` runs LICENSED.

A ``type: "test"`` license enables every feature, requires no Ed25519
signature (the signing private key is never shipped), and is intentionally
invalid for production use: it is host-restricted to loopback and raises the
client's provisional-license banner. It exists only so CI can exercise the
licensed code paths.

On a protected branch CI may instead provide a real ``$PLEXICHAT_LICENSE``;
the jobs prefer that and only fall back to this helper when it is unset.

Usage::

    python ci-tooling/write_test_license.py
    python ci-tooling/write_test_license.py --path /tmp/license.json
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# The repo root itself must be importable so the ``src`` package resolves;
# the two sub-paths mirror the layout ``src.utils.common_utils.utils.licensing``
# expects (same set pytest.ini puts on pythonpath).
for _path in (
    REPO_ROOT,
    REPO_ROOT / "src",
    REPO_ROOT / "src" / "utils",
    REPO_ROOT / "src" / "utils" / "common_utils",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# Import the canonical licensing module directly: the ``utils.licensing``
# shim re-exports the public surface, but generate_test_license_dict lives on
# the real module and importing it here avoids depending on shim internals.
import src.utils.common_utils.utils.licensing as licensing  # noqa: E402

DEFAULT_LICENSE_PATH = Path.home() / ".plexichat" / "config" / "license"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Write the provisional CI test license to the default license "
            "location so the self-test runs licensed."
        )
    )
    parser.add_argument(
        "--path",
        default=str(DEFAULT_LICENSE_PATH),
        help=f"Destination license path (default: {DEFAULT_LICENSE_PATH})",
    )
    args = parser.parse_args(argv)

    target = Path(args.path)
    license_dict = licensing.generate_test_license_dict(
        instance_id="ci-selftest", days_valid=1
    )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(license_dict, handle, indent=2)
    except OSError as exc:
        print(f"ERROR: cannot write test license to {target}: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote provisional test license (type={license_dict['type']}) to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
