#!/usr/bin/env python3
"""Accumulate per-job test-duration metrics and render an HTML trend report.

The ``performance:no_slow_tests`` job emits ``test-durations.json`` (one
metrics object per job) as a pipeline artifact. This script merges those
snapshots into a JSONL history committed to the ``ci-metrics`` branch and
renders ``index.html`` on that branch, so per-job test durations trend across
pipelines and regressions show over time.

Usage::

    python scripts/trend_publish.py merge --metrics test-durations.json \\
        --history ci-metrics.jsonl --out ci-metrics.jsonl

    python scripts/trend_publish.py render --history ci-metrics.jsonl \\
        --out index.html --project "$CI_PROJECT_PATH" --gate 5.0

Stdlib only (no dependencies), so the CI job needs just python + git.
"""

import argparse
import html
import json
import math
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

MAX_RUNS_PER_JOB = 200
DISPLAY_RUNS_PER_JOB = 50

CSS = """
:root {
  --bg: #0d1117; --panel: #161b22; --border: #30363d;
  --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
  --red: #f85149; --green: #3fb950; --amber: #d29922;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { margin: 0 0 4px; font-size: 24px; }
.subtitle { color: var(--muted); margin: 0 0 24px; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 28px; }
.chip { background: var(--panel); border: 1px solid var(--border);
  border-radius: 999px; padding: 4px 12px; color: var(--muted); font-size: 12px; }
section { margin-bottom: 36px; }
h2 { font-size: 18px; margin: 0 0 4px; }
.job-note { color: var(--muted); font-size: 12px; margin: 0 0 12px; }
table { width: 100%; border-collapse: collapse; background: var(--panel);
  border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums; white-space: nowrap; }
th { color: var(--muted); font-weight: 600; font-size: 12px;
  background: rgba(255,255,255,0.02); }
tr:last-child td { border-bottom: none; }
td.name { max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar { position: relative; min-width: 120px; height: 16px; background: rgba(255,255,255,0.06);
  border-radius: 4px; overflow: hidden; }
.bar i { position: absolute; inset: 0 auto 0 0; background: var(--accent); border-radius: 4px; }
.bar span { position: relative; z-index: 1; font-size: 11px; padding: 0 6px; }
.up { color: var(--red); } .down { color: var(--green); } .flat { color: var(--muted); }
.bad { color: var(--red); font-weight: 600; }
.warn { color: var(--amber); }
footer { color: var(--muted); font-size: 12px; margin-top: 24px; }
a { color: var(--accent); text-decoration: none; }
"""


def _as_float(value: Any, default: float = 0.0) -> float:
    """Coerce a JSON value to a finite float, tolerating corrupt entries."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _as_int(value: Any, default: int = 0) -> int:
    """Coerce a JSON value to an int, tolerating corrupt entries."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dedupe_key(entry: Dict[str, Any]) -> Tuple[str, str]:
    """Uniqueness key for a metrics entry.

    CI snapshots are keyed by ``(job, pipeline_id)``. Local runs have no
    pipeline_id, so they fall back to ``(job, timestamp)`` (millisecond
    precision in the emitter) instead of all collapsing onto one slot.
    """
    job = str(entry.get("job") or "unknown")
    pipeline_id = entry.get("pipeline_id")
    if pipeline_id:
        return (job, str(pipeline_id))
    return (job, str(entry.get("timestamp") or "no-timestamp"))


def _load_entries(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    entries: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _write_entries(path: str, entries: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, separators=(",", ":")) + "\n")


def merge(metrics_path: str, history_path: str, out_path: str) -> int:
    """Append new per-job metrics to the JSONL history, deduped and capped."""
    entries = _load_entries(history_path)
    seen = {_dedupe_key(e) for e in entries}

    # Tolerant merge: a missing or unparseable metrics artifact (e.g. the
    # performance job was interrupted) must not fail the trend job — the next
    # green run catches the history up.
    if not os.path.exists(metrics_path):
        print(
            f"WARN: metrics file {metrics_path} missing; skipping merge",
            file=sys.stderr,
        )
        return 0
    try:
        with open(metrics_path, "r", encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARN: cannot read metrics {metrics_path}: {exc}; skipping merge",
            file=sys.stderr,
        )
        return 0
    new_entries = snapshot if isinstance(snapshot, list) else [snapshot]

    added = 0
    for entry in new_entries:
        if not isinstance(entry, dict):
            continue
        key = _dedupe_key(entry)
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
        added += 1

    # Cap per job (keeping the newest runs) so the history cannot grow unbounded.
    per_job: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        per_job.setdefault(str(entry.get("job") or "unknown"), []).append(entry)
    capped: List[Dict[str, Any]] = []
    for job_entries in per_job.values():
        capped.extend(job_entries[-MAX_RUNS_PER_JOB:])

    _write_entries(out_path, capped)
    print(
        f"trend: merged {added} new metric(s) into {out_path} "
        f"({len(capped)} entries across {len(per_job)} job(s))"
    )
    return 0


def _fmt_seconds(value: Any) -> str:
    seconds = _as_float(value, default=math.nan)
    if not math.isfinite(seconds):
        return "—"
    if seconds >= 100:
        return f"{seconds:,.0f}s"
    return f"{seconds:.1f}s"


def _fmt_delta(current: float, previous: Optional[float]) -> str:
    if previous is None:
        return "—"
    diff = current - previous
    if abs(diff) < 0.05:
        return '<span class="flat">0.0s</span>'
    sign = "+" if diff > 0 else "−"
    cls = "up" if diff > 0 else "down"
    return f'<span class="{cls}">{sign}{abs(diff):.1f}s</span>'


def _pipeline_link(entry: Dict[str, Any], project: str, base_url: str) -> str:
    pipeline_id = str(entry.get("pipeline_id") or "")
    ref = str(entry.get("ref") or "")
    if pipeline_id and project and base_url:
        url = f"{base_url}/{project}/-/pipelines/{pipeline_id}"
        return (
            f'<a href="{html.escape(url, quote=True)}">{html.escape(pipeline_id)}</a>'
        )
    return html.escape(pipeline_id or ref or "—")


def _render_table(
    job_entries: List[Dict[str, Any]], gate: float, project: str, base_url: str
) -> str:
    rows = list(reversed(job_entries[-DISPLAY_RUNS_PER_JOB:]))
    max_total = max((_as_float(e.get("total_seconds")) for e in rows), default=1.0)
    if max_total <= 0:
        max_total = 1.0

    parts = [
        "<table><thead><tr>",
        "<th>Run</th><th>Pipeline</th><th>Total time</th><th>vs prev</th>",
        "<th>Worst test</th><th>Tests</th><th>Slow (&gt; gate)</th>",
        "</tr></thead><tbody>",
    ]
    for index, entry in enumerate(rows):
        total = _as_float(entry.get("total_seconds"))
        worst_seconds = _as_float(entry.get("worst_test_seconds"))
        worst_test = str(entry.get("worst_test") or "")
        width = min(100.0, (total / max_total) * 100.0)
        worst_cls = (
            "bad"
            if worst_seconds >= gate
            else ("warn" if worst_seconds >= gate * 0.8 else "")
        )
        worst_cell = "—"
        if worst_test:
            label = worst_test if len(worst_test) <= 70 else worst_test[:67] + "…"
            worst_cell = (
                f'<span class="{worst_cls}">{_fmt_seconds(worst_seconds)}</span>'
                f' <span class="name" title="{html.escape(worst_test, quote=True)}">'
                f"{html.escape(label)}</span>"
            )
        previous = (
            _as_float(rows[index + 1].get("total_seconds"))
            if index + 1 < len(rows)
            else None
        )
        parts.append(
            "<tr>"
            f"<td>{len(rows) - index}</td>"
            f"<td>{_pipeline_link(entry, project, base_url)}</td>"
            f'<td><div class="bar"><i style="width:{width:.1f}%"></i>'
            f"<span>{_fmt_seconds(total)}</span></div></td>"
            f"<td>{_fmt_delta(total, previous)}</td>"
            f"<td>{worst_cell}</td>"
            f"<td>{_as_int(entry.get('test_count'))}</td>"
            f"<td>{_as_int(entry.get('slow_count'))}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def render(
    history_path: str, out_path: str, gate: float, project: str, base_url: str
) -> int:
    """Render the JSONL history as a self-contained HTML trend report."""
    entries = _load_entries(history_path)
    jobs: List[str] = []
    per_job: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        job = str(entry.get("job") or "unknown")
        if job not in per_job:
            jobs.append(job)
            per_job[job] = []
        per_job[job].append(entry)

    sections = []
    for job in jobs:
        job_entries = per_job[job]
        latest = job_entries[-1]
        total = _as_float(latest.get("total_seconds"))
        worst = _as_float(latest.get("worst_test_seconds"))
        note = (
            f"{len(job_entries)} runs tracked · latest {_fmt_seconds(total)} total, "
            f"worst test {_fmt_seconds(worst)}"
        )
        sections.append(
            f"<section><h2>{html.escape(job)}</h2>"
            f'<p class="job-note">{html.escape(note)}</p>'
            f"{_render_table(job_entries, gate, project, base_url)}</section>"
        )

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total_runs = len(entries)
    generated = (
        f"Generated {html.escape(now)} · {total_runs} job-run(s) recorded · "
        f"gate threshold {gate:g}s"
    )

    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Test Duration Trends</title><style>" + CSS + "</style></head><body>"
        "<main>"
        f"<h1>Test Duration Trends</h1>"
        f'<p class="subtitle">{html.escape(project or "")}</p>'
        f'<div class="chips"><span class="chip">{html.escape(generated)}</span></div>'
        + "".join(sections)
        + "<footer>History is stored on the <code>ci-metrics</code> branch as "
        "<code>ci-metrics.jsonl</code> (machine-readable) with this report as "
        "<code>index.html</code>.</footer></main></body></html>"
    )
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(page)
    print(f"trend: rendered {out_path} ({len(jobs)} job(s), {total_runs} run(s))")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    merge_p = sub.add_parser("merge", help="append metrics to JSONL history")
    merge_p.add_argument(
        "--metrics", required=True, help="test-durations.json artifact"
    )
    merge_p.add_argument("--history", default="ci-metrics.jsonl")
    merge_p.add_argument("--out", default="ci-metrics.jsonl")

    render_p = sub.add_parser("render", help="render HTML trend report")
    render_p.add_argument("--history", default="ci-metrics.jsonl")
    render_p.add_argument("--out", default="index.html")
    render_p.add_argument("--gate", type=float, default=5.0)
    render_p.add_argument("--project", default=os.environ.get("CI_PROJECT_PATH", ""))
    render_p.add_argument("--base-url", default=os.environ.get("CI_SERVER_URL", ""))

    args = parser.parse_args(argv)
    if args.command == "merge":
        return merge(args.metrics, args.history, args.out)
    if args.command == "render":
        return render(args.history, args.out, args.gate, args.project, args.base_url)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
