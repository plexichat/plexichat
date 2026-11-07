#!/usr/bin/env python3
"""Generate SBOM/provenance artifacts for Plexichat (pragmatic, tool-aware).

Prefers tools available in the environment; degrades gracefully with clear
diagnostics. Intended for CI wiring and local/operator use.

Outputs (default: ./sbom/):
- python.cdx.json          CycloneDX SBOM for Python deps (cyclonedx-py or pip)
- python.freeze.txt        pip freeze snapshot / provenance input
- compose-images.txt       image references extracted from docker-compose.yml
- image-digests.lock.json  resolved repo digests for compose images (docker)
- <image>.cdx.json         per-image SBOM via syft/trivy when available

Usage:
    python scripts/generate_sbom.py
    python scripts/generate_sbom.py --compose docker-compose.yml --out sbom
    python scripts/generate_sbom.py --images-only --require-tool

Exit 0 on success (including partial generation with warnings), 1 on hard
failure, 2 on usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGE_LINE_RE = re.compile(r"(?m)^\s*image:\s*(\S+)\s*$")


def run(
    cmd: list[str], *, cwd: Path | None = None, capture: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        text=True,
        capture_output=capture,
        check=False,
    )


def which_any(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def expand_compose_images(compose_path: Path) -> list[str]:
    if not compose_path.is_file():
        return []
    text = compose_path.read_text(encoding="utf-8", errors="replace")
    images = []
    for m in IMAGE_LINE_RE.finditer(text):
        ref = m.group(1)
        # Expand common defaults for provenance snapshots (best-effort).
        ref = ref.replace(
            "${CI_REGISTRY_IMAGE:-gitlab.plexichat.com/plexichat/plexichat}",
            "gitlab.plexichat.com/plexichat/plexichat",
        )
        ref = ref.replace(
            "${CI_REGISTRY_IMAGE:-gitlab.plexichat.com/plexichat/plexichat-client}",
            "gitlab.plexichat.com/plexichat/plexichat-client",
        )
        ref = os.path.expandvars(ref)
        if "${" in ref:
            # Unresolved vars: keep template as provenance note.
            images.append(ref)
        else:
            images.append(ref)
    # de-dupe, preserve order
    seen = set()
    out = []
    for i in images:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def python_sbom(out: Path, warnings: list[str]) -> None:
    freeze = run([sys.executable, "-m", "pip", "freeze"])
    write_text(out / "python.freeze.txt", (freeze.stdout or "") + (freeze.stderr or ""))

    # cyclonedx-py (CycloneDX Python tool) variants.
    for cmd in (
        [
            sys.executable,
            "-m",
            "cyclonedx_py",
            "environment",
            "--of",
            "JSON",
            "-o",
            str(out / "python.cdx.json"),
        ],
        [
            "cyclonedx-py",
            "environment",
            "--of",
            "JSON",
            "-o",
            str(out / "python.cdx.json"),
        ],
        ["cyclonedx-bom", "-e", "-o", str(out / "python.cdx.json"), "--format", "json"],
    ):
        if which_any(cmd[0]) or cmd[0] == sys.executable:
            p = run(cmd)
            if p.returncode == 0 and (out / "python.cdx.json").is_file():
                return
    # Minimal CycloneDX-ish fallback from freeze for provenance continuity.
    deps = []
    for line in (freeze.stdout or "").splitlines():
        line = line.strip()
        if "==" in line and not line.startswith("#"):
            name, ver = line.split("==", 1)
            deps.append(
                {"name": name, "version": ver, "purl": f"pkg:pypi/{name}@{ver}"}
            )
    fallback = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tools": [{"name": "plexichat-generate-sbom-fallback"}],
            "component": {
                "type": "application",
                "name": "plexichat",
                "version": "unknown",
            },
        },
        "components": [
            {
                "type": "library",
                "name": d["name"],
                "version": d["version"],
                "purl": d["purl"],
            }
            for d in deps
        ],
    }
    write_text(
        out / "python.cdx.json", json.dumps(fallback, indent=2, sort_keys=True) + "\n"
    )
    warnings.append(
        "cyclonedx-py not available; wrote fallback Python SBOM from pip freeze"
    )


def resolve_digests(images: list[str], out: Path, warnings: list[str]) -> dict:
    lock = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "images": {},
    }
    docker = which_any("docker")
    for ref in images:
        if "${" in ref:
            lock["images"][ref] = {"status": "unresolved-template"}
            continue
        entry: dict = {"reference": ref}
        if docker:
            insp = run(
                [docker, "image", "inspect", "--format", "{{json .RepoDigests}}", ref]
            )
            if insp.returncode == 0:
                try:
                    digests = json.loads(insp.stdout.strip() or "[]")
                except Exception:
                    digests = []
                if digests:
                    entry["repo_digests"] = digests
                    # Prefer first digest as canonical pin target.
                    m = re.search(r"@sha256:[0-9a-f]{64}", digests[0])
                    if m:
                        entry["digest"] = m.group(0)[1:]
            if "digest" not in entry:
                # Try manifest inspect for remote digest (best-effort).
                mi = run(
                    [
                        docker,
                        "buildx",
                        "imagetools",
                        "inspect",
                        ref,
                        "--format",
                        "{{json .Manifest.Digest}}",
                    ]
                )
                if mi.returncode == 0 and mi.stdout.strip().strip('"').startswith(
                    "sha256:"
                ):
                    entry["digest"] = mi.stdout.strip().strip('"')
        if "digest" not in entry and "repo_digests" not in entry:
            entry["status"] = "digest-unresolved"
            warnings.append(f"could not resolve digest for image: {ref}")
        lock["images"][ref] = entry
    write_text(
        out / "image-digests.lock.json",
        json.dumps(lock, indent=2, sort_keys=True) + "\n",
    )
    return lock


def safe_name(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", ref).strip("_")[:180] or "image"


def image_sbom(ref: str, out: Path, warnings: list[str]) -> None:
    if "${" in ref:
        return
    name = safe_name(ref)
    target = out / f"image.{name}.cdx.json"
    syft = which_any("syft")
    trivy = which_any("trivy")
    if syft:
        p = run([syft, ref, "-o", f"cyclonedx-json={target}"])
        if p.returncode == 0 and target.is_file():
            return
        warnings.append(
            f"syft failed for {ref}: {(p.stderr or p.stdout).strip()[:200]}"
        )
    if trivy:
        # Trivy can emit CycloneDX for images.
        p = run(
            [
                trivy,
                "image",
                "--format",
                "cyclonedx",
                "--output",
                str(target),
                "--quiet",
                ref,
            ]
        )
        if p.returncode == 0 and target.is_file():
            return
        warnings.append(
            f"trivy image SBOM failed for {ref}: {(p.stderr or p.stdout).strip()[:200]}"
        )
    if not syft and not trivy:
        warnings.append(f"no syft/trivy available for image SBOM: {ref}")


def provenance(out: Path, compose: Path, images: list[str]) -> None:
    payload = {
        "type": "https://plexichat.com/provenance/build-snapshot/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "compose_file": str(compose),
        "images": images,
        "git": {},
    }
    git_sha = run(["git", "rev-parse", "HEAD"])
    if git_sha.returncode == 0:
        payload["git"]["commit"] = git_sha.stdout.strip()
    git_desc = run(["git", "describe", "--always", "--dirty", "--tags"])
    if git_desc.returncode == 0:
        payload["git"]["describe"] = git_desc.stdout.strip()
    write_text(
        out / "provenance.json", json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compose", default=str(REPO_ROOT / "docker-compose.yml"))
    ap.add_argument("--out", default=str(REPO_ROOT / "sbom"))
    ap.add_argument("--images-only", action="store_true")
    ap.add_argument("--python-only", action="store_true")
    ap.add_argument(
        "--no-image-sbom",
        action="store_true",
        help="resolve digests but skip per-image SBOM generation",
    )
    ap.add_argument(
        "--require-tool",
        action="store_true",
        help="fail if no SBOM tool is available for requested mode",
    )
    args = ap.parse_args(argv)

    out = Path(args.out)
    ensure_dir(out)
    compose = Path(args.compose)
    warnings: list[str] = []
    images = expand_compose_images(compose)
    write_text(out / "compose-images.txt", "\n".join(images) + ("\n" if images else ""))

    if not args.images_only:
        python_sbom(out, warnings)

    if not args.python_only:
        resolve_digests(images, out, warnings)
        if not args.no_image_sbom:
            for ref in images:
                image_sbom(ref, out, warnings)

    provenance(out, compose, images)

    if args.require_tool:
        if not args.images_only and not (out / "python.cdx.json").is_file():
            print("ERROR: Python SBOM not generated", file=sys.stderr)
            return 1
        if not args.python_only and not args.no_image_sbom:
            has_image = any(p.name.startswith("image.") for p in out.iterdir())
            if (
                images
                and any("${" not in i for i in images)
                and not has_image
                and not which_any("syft", "trivy")
            ):
                print(
                    "ERROR: no image SBOM tool (syft/trivy) available", file=sys.stderr
                )
                return 1

    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)
    print(f"SBOM/provenance artifacts written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
