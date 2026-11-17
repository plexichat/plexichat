#!/usr/bin/env python3
"""Enforce least-privilege CI job permissions and bounded token scopes.

GitLab hands every job a CI_JOB_TOKEN and inherits group/project variables by
default. A contributor who adds a job that reuses the merge-request PAT, pushes
to a protected ref, or mints an OIDC token without an audience silently widens
the pipeline's blast radius. This checker reads a CI file (as text, so it runs
in a bare python:3.14-slim image) and fails when:

* a privileged variable is used outside the job allowlist in the policy;
* an ``id_tokens`` entry has no ``aud`` (an unbounded OIDC audience); or
* a job other than the declared write jobs runs ``git push``.

The policy lives in ``ci-permissions-policy.json`` next to this script; changing
it is a reviewed change to the token budget itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_POLICY = Path(__file__).with_name("ci-permissions-policy.json")
TOKEN_LINE = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*):\s*(?:#.*)?$")
AUD_RE = re.compile(r"^\s+aud:\s*(?P<value>\S+)")
PUSH_RE = re.compile(r"\bgit\s+push\b")


class Job:
    """A top-level CI key with its indented body."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.body: list[str] = []

    @property
    def text(self) -> str:
        return "\n".join(self.body)


def parse_jobs(text: str) -> dict[str, Job]:
    """Extract top-level keys and their bodies without a YAML dependency."""
    jobs: dict[str, Job] = {}
    current: Job | None = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and line.rstrip().endswith(":"):
            current = Job(line.rstrip()[:-1])
            jobs[current.name] = current
        elif current is not None:
            current.body.append(line)
    return jobs


def _is_job(job: Job) -> bool:
    body = job.text
    return any(token in body for token in ("stage:", "script:", "extends:", "trigger:"))


def check_id_tokens(job: Job, required_aud: str, errors: list[str]) -> None:
    """An OIDC token must be bound to exactly the audience the policy declares.

    A wildcard (``*``) or an arbitrary https:// audience is *not* bounded: any
    peer that can obtain a token for that audience may exchange it. The earlier
    implementation also broke out of the aud scan on the token-name line itself,
    so it never read the declared audience at all -- the policy check was dead
    code and any real id_tokens block was rejected for the wrong reason. Read
    each token's own indented block instead and require an exact match to the
    declared audience.
    """
    lines = job.body
    for index, line in enumerate(lines):
        if not re.match(r"^\s{2}id_tokens:\s*(?:#.*)?$", line):
            continue
        # Tokens are the first nested level under ``id_tokens``. Remember that
        # indent so the token's own ``aud:`` child is not mistaken for a token.
        token_indent: int | None = None
        tokens: list[tuple[int, str, int]] = []
        for offset, nested in enumerate(lines[index + 1 :], start=index + 1):
            stripped = nested.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(nested) - len(nested.lstrip())
            if indent <= 2:
                break
            if token_indent is None:
                token_indent = indent
            if indent != token_indent:
                continue
            match = TOKEN_LINE.match(nested)
            if match:
                tokens.append((offset, match.group("name"), indent))
        if not tokens:
            errors.append(f"{job.name}: id_tokens declares no token")
        for tok_index, name, indent in tokens:
            audience: str | None = None
            # Scan only this token's block: stop at the next sibling token or
            # at the end of the id_tokens mapping.
            for nested in lines[tok_index + 1 :]:
                stripped = nested.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if len(nested) - len(nested.lstrip()) <= indent:
                    break
                aud = AUD_RE.match(nested)
                if aud:
                    audience = aud.group("value").strip("'\"")
                    break
            if not audience:
                errors.append(
                    f"{job.name}: id token {name!r} has no aud; token scope is unbounded"
                )
            elif audience == "*":
                errors.append(
                    f"{job.name}: id token {name!r} uses wildcard audience '*'; token scope is unbounded"
                )
            elif required_aud and audience != required_aud:
                errors.append(
                    f"{job.name}: id token {name!r} audience {audience!r} is outside the declared policy audience"
                )


def check(repo: Path, policy_path: Path, ci_files: list[Path]) -> list[str]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    privileged = set(policy["privileged_vars"])
    var_allowlist: dict[str, set[str]] = {
        job: set(vars_) for job, vars_ in policy["var_allowlist"].items()
    }
    write_jobs = set(policy["write_jobs"])
    required_aud = policy.get("required_aud", "")

    errors: list[str] = []
    for ci in ci_files:
        path = repo / ci
        if not path.is_file():
            errors.append(f"{ci}: CI file is missing")
            continue
        for name, job in parse_jobs(path.read_text(encoding="utf-8")).items():
            body = job.text
            used = {
                var for var in privileged if re.search(rf"\b{re.escape(var)}\b", body)
            }
            if not _is_job(job):
                # A privileged variable declared outside a job (most notably in
                # a global ``variables:`` block) is inherited by every job and
                # can be referenced through an alias, escaping the per-job
                # allowlist entirely.
                for var in sorted(used):
                    errors.append(
                        f"{ci}:{name}: privileged variable {var} is declared outside a job; it would be inherited by every job"
                    )
                continue
            for var in sorted(used - var_allowlist.get(name, set())):
                errors.append(
                    f"{ci}:{name}: privileged variable {var} is not allowed for this job"
                )
            for var in sorted(var_allowlist.get(name, set())):
                if var not in used:
                    errors.append(
                        f"{ci}:{name}: declared privileged variable {var} is no longer used; narrow the policy"
                    )
            if PUSH_RE.search(body) and name not in write_jobs:
                errors.append(
                    f"{ci}:{name}: runs 'git push' but is not a declared write job"
                )
            check_id_tokens(job, required_aud, errors)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="repository root (default: cwd)"
    )
    parser.add_argument(
        "--policy", type=Path, default=DEFAULT_POLICY, help="permission policy JSON"
    )
    parser.add_argument(
        "--ci",
        action="append",
        default=None,
        help="CI file relative to --repo (repeatable; default: .gitlab-ci.yml and .gitlab/ci/*.yml)",
    )
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    ci_files = args.ci
    if not ci_files:
        ci_files = [".gitlab-ci.yml"]
        included = sorted((repo / ".gitlab" / "ci").glob("*.yml"))
        ci_files.extend(str(path.relative_to(repo)) for path in included)

    errors = check(repo, args.policy.resolve(), [Path(ci) for ci in ci_files])
    if errors:
        print("FAIL: CI permissions policy violated", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(
        "CI permissions OK: privileged variables and writes are scoped to declared jobs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
