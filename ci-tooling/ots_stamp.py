#!/usr/bin/env python3
"""Create an OpenTimestamps proof for the current CI commit.

Reads ``CI_COMMIT_SHA`` / ``CI_PROJECT_PATH`` from the environment and writes
``ots-proofs/<project>_<sha12>.ots``. Shared by the ``ots:timestamp_commit``
and ``ots:push_proofs`` GitLab CI jobs (previously duplicated inline heredocs).
"""

from __future__ import annotations

import hashlib
import os
import sys

from opentimestamps.calendar import RemoteCalendar
from opentimestamps.core.op import OpSHA256
from opentimestamps.core.serialize import StreamSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp


def main() -> int:
    commit_sha = os.environ["CI_COMMIT_SHA"]
    project = os.environ["CI_PROJECT_PATH"].replace("/", "_")
    sha256 = hashlib.sha256(commit_sha.encode()).digest()

    stamp = Timestamp(sha256)
    dtf = DetachedTimestampFile(OpSHA256(), stamp)

    calendars = [
        "https://ots.btc.catallaxy.com",
        "https://a.calendar.opentimestamps.org",
        "https://b.calendar.opentimestamps.org",
    ]
    for cal_url in calendars:
        try:
            cal = RemoteCalendar(cal_url)
            cal_stamp = cal.submit(sha256, timeout=30)
            dtf.timestamp.merge(cal_stamp)
            print(f"Submitted to {cal_url}")
            break
        except Exception as e:  # noqa: BLE001 - try the next calendar
            print(f"Calendar {cal_url} failed: {e}", file=sys.stderr)
            continue

    out_dir = "ots-proofs"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{project}_{commit_sha[:12]}.ots")
    with open(out_path, "wb") as f:
        ctx = StreamSerializationContext(f)
        dtf.serialize(ctx)
    print(f"Proof saved: {out_path} ({os.path.getsize(out_path)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
