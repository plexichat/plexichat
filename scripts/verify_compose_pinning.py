#!/usr/bin/env python3
"""Lightweight compose pinning guard (stdlib-only, fast CI gate).

Fails when:
- any third-party image uses :latest
- first-party backend/client/backup images use :-latest or lack an explicit
  required tag/digest variable (:${VAR:?...}) unless ALLOW_UNPINNED_IMAGES=1
- ALLOW_DEV_COMPOSE/ALLOW_DOCKERHUB_FALLBACK mentions are removed from policy
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"


def main() -> int:
    allow = os.environ.get("ALLOW_UNPINNED_IMAGES", "") in {"1", "true", "TRUE", "yes"}
    text = COMPOSE.read_text(encoding="utf-8")
    errors = []
    for req in (
        "ALLOW_DEV_COMPOSE",
        "ALLOW_DOCKERHUB_FALLBACK",
        "ALLOW_UNPINNED_IMAGES",
    ):
        if req not in text:
            errors.append(f"policy marker missing from compose header: {req}")
    for m in re.finditer(r"(?m)^\s*image:\s*(\S+)\s*$", text):
        img = m.group(1)
        if "${" in img:
            if not allow and re.search(r":-\s*latest\b", img):
                errors.append(f"unpinned first-party default (:latest fallback): {img}")
            if "/backend:" in img or "/nginx:" in img or "/backup:" in img:
                if ":?" not in img and "@sha256:" not in img and not allow:
                    errors.append(
                        f"first-party image must require pinned tag/digest (use :${{VAR:?..}} or @sha256): {img}"
                    )
            continue
        if img.endswith(":latest"):
            errors.append(f"floating third-party image tag: {img}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print("compose pinning guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
