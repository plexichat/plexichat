"""Pin the analyzer jobs to an image that actually contains /analyzer.

``security:sast`` declared no ``image:``, so it inherited the pipeline default
(``python:3.11-bullseye``) and ran ``/analyzer run`` there. The tag pipeline
died in a second on ``/bin/bash: line 1: /analyzer: No such file or directory``
(exit 127) -- and because the job is advisory, the pipeline still reported
success, so the release was never scanned. A job that executes the analyzer has
to name the analyzer image; that is cheap to check and impossible to notice by
reading the green pipeline.
"""

from pathlib import Path

import yaml

_PIPELINE = Path(__file__).resolve().parents[3] / ".gitlab" / "ci" / "pipeline.yml"


def _jobs():
    for name, job in yaml.safe_load(_PIPELINE.read_text(encoding="utf-8")).items():
        if isinstance(job, dict):
            yield name, job


def test_every_analyzer_job_names_its_own_image():
    offenders = []
    for name, job in _jobs():
        script = job.get("script") or []
        steps = [script] if isinstance(script, str) else list(script)
        if not any("/analyzer" in str(step) for step in steps):
            continue
        image = str(job.get("image") or "")
        if "security-products" not in image and "analyzers/" not in image:
            offenders.append(f"{name}: {image or 'no image (inherits the default)'}")
    assert offenders == [], (
        "these jobs run /analyzer without an analyzer image: " + "; ".join(offenders)
    )
