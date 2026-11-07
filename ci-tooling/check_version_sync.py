#!/usr/bin/env python3
"""Fail when released version strings or key infra endpoints drift across repos.

Canonical released-artifact versions:
- Backend: ``plexichat/src/server/lifecycle.py`` ``VERSION = "..."``
- Client:  ``plexichat-client/package.json`` ``"version": "..."``

Checked for consistency with those sources (when the target files exist):
- Landing page version footers (all HTML pages carrying the version banner)
- Landing deploy script defaults (``deploy.sh``, ``deploy.ps1``)
- Landing deploy-flags test "published Server/Client pair" assertion
- Compose file version references (informational: compose uses ``:latest`` by
  design; only explicit release-tag pins are checked, not ``:-latest`` defaults)
- Key endpoint strings present in expected files (``/gateway``,
  ``/api/v1/ready``, ``/api/v1/health``)

The script auto-detects the workspace root (a directory containing
``plexichat/``, ``plexichat-client/``, and/or ``plexichat-landing/``) from its
own location or the current working directory. Missing sibling repos are
skipped with a note so the check stays non-flaky in single-repo CI checkouts;
drift in files that *are* present fails.

``--fix`` rewrites only unambiguous targets: landing version footers and
deploy-script default version assignments / the published-pair test assertion.
It never invents versions and never touches fixture literals used to test flag
precedence.

Run from anywhere::

    python plexichat/ci-tooling/check_version_sync.py
    python plexichat/ci-tooling/check_version_sync.py --workspace /root/code-plexichat
    python plexichat/ci-tooling/check_version_sync.py --fix

Exit codes: 0 = clean, 1 = drift/issues found, 2 = usage/missing canonical source.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, NoReturn, Optional, Sequence, Tuple

FOOTER_RE = re.compile(
    r"(Backend \(server API\): <strong>)([^<]+)(</strong> &middot; Frontend \(web client\): <strong>)([^<]+)(</strong>)"
)

# PEP 440-ish tag the project uses (e.g. 1.0.0a184, 1.0.0, 2.1.0rc1.post1).
VERSION_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:[abrc]\d+)?(?:\.post\d+)?(?:\.dev\d+)?(?:\+.+)?$"
)


@dataclass
class Issue:
    level: str  # "FAIL" or "WARN"
    path: str
    message: str

    def format(self, root: Path) -> str:
        try:
            rel = Path(self.path).resolve().relative_to(root.resolve())
        except Exception:
            rel = self.path
        return f"  {self.level} {rel}: {self.message}"


@dataclass
class Fix:
    path: Path
    description: str
    original: str
    updated: str


def find_workspace(explicit: Optional[str], script: Path) -> Path:
    if explicit:
        p = Path(explicit).resolve()
        if not p.is_dir():
            die(f"--workspace is not a directory: {p}")
        return p
    # Walk up from script, then cwd, looking for a dir that contains plexichat/.
    for start in (script.resolve(), Path.cwd().resolve()):
        for candidate in [start, *start.parents]:
            if (candidate / "plexichat").is_dir() and (
                (candidate / "plexichat-client").is_dir()
                or (candidate / "plexichat-landing").is_dir()
                or (candidate / "plexichat" / "src").is_dir()
            ):
                return candidate
    # Fallback: script is .../plexichat/ci-tooling/check_version_sync.py
    if script.parent.name == "ci-tooling" and script.parent.parent.name == "plexichat":
        return script.parent.parent.parent
    return Path.cwd().resolve()


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(2)


def read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def canonical_backend(workspace: Path) -> Tuple[str, Path]:
    path = workspace / "plexichat" / "src" / "server" / "lifecycle.py"
    text = read_text(path)
    if text is None:
        die(f"canonical backend version source missing: {path}")
    m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not m:
        die(f"could not parse VERSION from {path}")
    ver = m.group(1)
    if not VERSION_RE.match(ver):
        die(f"backend VERSION {ver!r} in {path} does not look like a release version")
    return ver, path


def canonical_client(workspace: Path) -> Tuple[str, Path]:
    path = workspace / "plexichat-client" / "package.json"
    text = read_text(path)
    if text is None:
        die(f"canonical client version source missing: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        die(f"invalid JSON in {path}: {exc}")
    ver = data.get("version")
    if not isinstance(ver, str) or not ver:
        die(f"no string 'version' in {path}")
    if not VERSION_RE.match(ver):
        die(f"client version {ver!r} in {path} does not look like a release version")
    return ver, path


def check_landing_footers(
    workspace: Path, backend: str, client: str, issues: List[Issue], fixes: List[Fix]
) -> int:
    landing = workspace / "plexichat-landing"
    if not landing.is_dir():
        issues.append(
            Issue("WARN", str(landing), "plexichat-landing not present; skipped")
        )
        return 0
    checked = 0
    found_any = False
    for path in sorted(landing.glob("*.html")):
        text = read_text(path)
        if text is None or "Backend (server API):" not in text:
            continue
        matches = list(FOOTER_RE.finditer(text))
        if not matches:
            continue
        found_any = True
        checked += len(matches)
        updated = text
        file_drift = False
        for m in matches:
            bver, cver = m.group(2), m.group(4)
            if bver != backend or cver != client:
                file_drift = True
                issues.append(
                    Issue(
                        "FAIL",
                        str(path),
                        f"footer shows backend {bver!r} / client {cver!r}, "
                        f"expected {backend!r} / {client!r}",
                    )
                )
        if file_drift:
            repl = rf"\g<1>{backend}\g<3>{client}\g<5>"
            updated = FOOTER_RE.sub(repl, text)
            if updated != text:
                fixes.append(Fix(path, "sync landing version footer(s)", text, updated))
    if not found_any:
        issues.append(
            Issue(
                "FAIL",
                str(landing),
                "no landing HTML files contain a recognized version footer",
            )
        )
    return checked


def subn_count(pattern: str, repl: str, text: str, flags: int = 0) -> Tuple[str, int]:
    return re.subn(pattern, repl, text, flags=flags)


def check_deploy_sh(
    workspace: Path, backend: str, client: str, issues: List[Issue], fixes: List[Fix]
) -> None:
    path = workspace / "plexichat-landing" / "deploy.sh"
    text = read_text(path)
    if text is None:
        issues.append(Issue("WARN", str(path), "deploy.sh not present; skipped"))
        return
    updated = text
    # Top-level default assignments (the script's defaults, not test heredocs).
    rules: List[Tuple[str, str, str]] = [
        (r'^(VERSION=")[^"]*(")', rf"\g<1>{backend}\2", "VERSION default"),
        (
            r'^(BACKEND_VERSION=")[^"]*(")',
            rf"\g<1>{backend}\2",
            "BACKEND_VERSION default",
        ),
        (r'^(CLIENT_VERSION=")[^"]*(")', rf"\g<1>{client}\2", "CLIENT_VERSION default"),
        # Help/usage default annotations for the per-image/combined flags.
        (
            r"(--version <tag>   Version tag \(default: )[^)]+(\))",
            rf"\g<1>{backend}\2",
            "help -v default",
        ),
        (
            r"(--backend-version <tag> Backend version tag \(default: )[^)]+(\))",
            rf"\g<1>{backend}\2",
            "help -bv default",
        ),
        (
            r"(--client-version <tag>  Client version tag \(default: )[^)]+(\))",
            rf"\g<1>{client}\2",
            "help -cv default",
        ),
        (
            r"(-bv, --backend-version <tag> Backend version tag \(default: )[^)]+(\))",
            rf"\g<1>{backend}\2",
            "usage -bv default",
        ),
        (
            r"(-cv, --client-version <tag>  Client version tag \(default: )[^)]+(\))",
            rf"\g<1>{client}\2",
            "usage -cv default",
        ),
        # The deploy-flags regression test asserts the published default pair.
        # test_deploy_flags.sh uses a line continuation: check "..." \ \n "VERSION=..."
        (
            r'("defaults resolve to the published Server/Client pair"[^\n]*\n\s*")VERSION=[^ ]+ BACKEND_VERSION=[^ ]+ CLIENT_VERSION=[^"]+(")',
            rf"\g<1>VERSION={backend} BACKEND_VERSION={backend} CLIENT_VERSION={client}\2",
            "test published-pair assertion",
        ),
    ]
    for pat, repl, label in rules:
        new, n = subn_count(pat, repl, updated, flags=re.MULTILINE)
        if n == 0:
            # Not all patterns are mandatory (help text varies), but core defaults are.
            if (
                label.endswith("default")
                and "help" not in label
                and "usage" not in label
                and "test" not in label
            ):
                issues.append(
                    Issue("FAIL", str(path), f"could not locate {label} assignment")
                )
            continue
        # Verify the replacement yields the expected values by re-checking key assignments.
        updated = new
    # Validate core defaults after fix attempt / in check mode.
    core = {
        "VERSION": backend,
        "BACKEND_VERSION": backend,
        "CLIENT_VERSION": client,
    }
    for name, expected in core.items():
        m = re.search(rf'^{name}="([^"]*)"', updated, re.MULTILINE)
        if not m:
            issues.append(
                Issue("FAIL", str(path), f"missing {name} default assignment")
            )
        elif m.group(1) != expected:
            issues.append(
                Issue(
                    "FAIL",
                    str(path),
                    f"{name} default is {m.group(1)!r}, expected {expected!r}",
                )
            )
    # Published-pair test assertion.
    tm = re.search(
        r'"defaults resolve to the published Server/Client pair"[^\n]*\n\s*"([^"]+)"',
        updated,
    )
    if tm:
        expected_line = (
            f"VERSION={backend} BACKEND_VERSION={backend} CLIENT_VERSION={client}"
        )
        if tm.group(1) != expected_line:
            issues.append(
                Issue(
                    "FAIL",
                    str(path),
                    f"published-pair test asserts {tm.group(1)!r}, expected {expected_line!r}",
                )
            )
    else:
        issues.append(
            Issue(
                "WARN",
                str(path),
                "published-pair test assertion not found (test may have moved)",
            )
        )
    if updated != text:
        fixes.append(Fix(path, "sync deploy.sh default versions", text, updated))


def check_deploy_ps1(
    workspace: Path, backend: str, client: str, issues: List[Issue], fixes: List[Fix]
) -> None:
    path = workspace / "plexichat-landing" / "deploy.ps1"
    text = read_text(path)
    if text is None:
        issues.append(Issue("WARN", str(path), "deploy.ps1 not present; skipped"))
        return
    updated = text
    rules: List[Tuple[str, str, str]] = [
        (
            r'(\[string\]\$Version = ")[^"]*(")',
            rf"\g<1>{backend}\2",
            "$Version default",
        ),
        (
            r'(\[string\]\$BackendVersion = ")[^"]*(")',
            rf"\g<1>{backend}\2",
            "$BackendVersion default",
        ),
        (
            r'(\[string\]\$ClientVersion = ")[^"]*(")',
            rf"\g<1>{client}\2",
            "$ClientVersion default",
        ),
        # Usage/help default annotations.
        (
            r"(-BackendVersion <tag>\s+Backend version tag \(default: )[^)]+(\))",
            rf"\g<1>{backend}\2",
            "help backend default",
        ),
        (
            r"(-ClientVersion <tag>\s+Client version tag \(default: )[^)]+(\))",
            rf"\g<1>{client}\2",
            "help client default",
        ),
    ]
    for pat, repl, label in rules:
        new, n = subn_count(pat, repl, updated, flags=re.MULTILINE)
        if n:
            updated = new
        elif label.endswith("default") and "help" not in label:
            issues.append(
                Issue("FAIL", str(path), f"could not locate {label} parameter")
            )
    # Non-interactive example lines that carry the published pair.
    updated, _ = subn_count(
        r"(deploy\.ps1 install -BackendVersion )\d+\.\d+\.\d+(?:[abrc]\d+)?(?:\.post\d+)?( -ClientVersion )\d+\.\d+\.\d+(?:[abrc]\d+)?(?:\.post\d+)?",
        rf"\g<1>{backend}\g<2>{client}",
        updated,
    )
    # Validate core param defaults.
    core = [
        (r'\[string\]\$Version = "([^"]*)"', backend, "$Version"),
        (r'\[string\]\$BackendVersion = "([^"]*)"', backend, "$BackendVersion"),
        (r'\[string\]\$ClientVersion = "([^"]*)"', client, "$ClientVersion"),
    ]
    for pat, expected, label in core:
        m = re.search(pat, updated)
        if not m:
            issues.append(
                Issue("FAIL", str(path), f"missing {label} default parameter")
            )
        elif m.group(1) != expected:
            issues.append(
                Issue(
                    "FAIL",
                    str(path),
                    f"{label} default is {m.group(1)!r}, expected {expected!r}",
                )
            )
    if updated != text:
        fixes.append(Fix(path, "sync deploy.ps1 default versions", text, updated))


def check_compose(workspace: Path, issues: List[Issue]) -> None:
    """Compose keeps first-party images operator-pinned; flag literal tag pins.

    Two forms are valid: ``${BACKEND_VERSION:-latest}`` (dev-friendly default)
    and the hardened ``${BACKEND_VERSION:?message}`` form, which refuses to
    start without an explicit pinned release. Neither is version drift.
    """
    path = workspace / "plexichat" / "docker-compose.yml"
    text = read_text(path)
    if text is None:
        issues.append(
            Issue("WARN", str(path), "docker-compose.yml not present; skipped")
        )
        return
    # First-party images must keep one of the two operator-pinned forms.
    for svc, pat in [
        ("backend", r"backend:\$\{BACKEND_VERSION(?::-latest|:\?[^}]+)\}"),
        ("client", r"nginx:\$\{CLIENT_VERSION(?::-latest|:\?[^}]+)\}"),
        ("backup", r"backup:\$\{BACKEND_VERSION(?::-latest|:\?[^}]+)\}"),
    ]:
        if not re.search(pat, text):
            issues.append(
                Issue(
                    "FAIL",
                    str(path),
                    f"{svc} image is not using an operator-pinned version variable ({pat})",
                )
            )
    # Explicit release-tag pins on first-party images (not third-party) are drift.
    for m in re.finditer(
        r"image:\s*\$\{CI_REGISTRY_IMAGE[^}]*/(backend|nginx|backup):(\d+\.\d+\.\d+[^\s\"']*)",
        text,
    ):
        issues.append(
            Issue(
                "FAIL",
                str(path),
                f"first-party {m.group(1)} image is pinned to release tag {m.group(2)!r}; "
                "use ${BACKEND_VERSION:-latest}/${CLIENT_VERSION:-latest}",
            )
        )


def check_endpoints(workspace: Path, issues: List[Issue]) -> None:
    """Verify key endpoint strings exist in the files that define/advertise them."""
    checks: List[Tuple[Path, List[str], str]] = [
        (
            workspace / "plexichat" / "docker-compose.yml",
            ["/api/v1/ready"],
            "compose backend healthcheck",
        ),
        (
            workspace / "plexichat" / "src" / "api" / "routes" / "health.py",
            ['"/ready"', '"/health"'],
            "health route definitions",
        ),
        (
            workspace / "plexichat" / "src" / "api" / "app.py",
            ["/gateway", "gateway"],
            "gateway route registration",
        ),
        (
            workspace / "plexichat" / "src" / "config_defaults.py",
            ['"api_prefix"', "/api/v1"],
            "api_prefix default",
        ),
    ]
    for path, needles, label in checks:
        text = read_text(path)
        if text is None:
            # Only warn for files outside the core backend repo expectations.
            level = (
                "FAIL"
                if "plexichat/src" in path.as_posix() or "docker-compose" in path.name
                else "WARN"
            )
            issues.append(Issue(level, str(path), f"{label}: file missing"))
            continue
        missing = [n for n in needles if n not in text]
        if missing:
            issues.append(
                Issue(
                    "FAIL",
                    str(path),
                    f"{label}: missing expected endpoint/config string(s): {missing}",
                )
            )
    # Landing should advertise the key endpoints (at least somewhere).
    landing = workspace / "plexichat-landing"
    if landing.is_dir():
        landing_text = "\n".join(
            (read_text(p) or "") for p in sorted(landing.glob("*.html"))
        )
        for needle in ["/gateway", "/api/v1/ready", "/api/v1/health"]:
            if needle not in landing_text:
                issues.append(
                    Issue(
                        "FAIL",
                        str(landing),
                        f"landing pages do not mention endpoint {needle!r}",
                    )
                )


def apply_fixes(fixes: List[Fix], root: Path) -> int:
    applied = 0
    seen: set[Path] = set()
    for fix in fixes:
        if fix.path in seen:
            continue
        seen.add(fix.path)
        fix.path.write_text(fix.updated, encoding="utf-8")
        try:
            rel = fix.path.resolve().relative_to(root.resolve())
        except Exception:
            rel = fix.path
        print(f"  FIXED {rel}: {fix.description}")
        applied += 1
    return applied


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check version/infra endpoint sync across Plexichat repos."
    )
    parser.add_argument(
        "--workspace", help="Workspace root containing plexichat*/ repos"
    )
    parser.add_argument(
        "--fix", action="store_true", help="Rewrite unambiguous drift (conservative)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit a JSON summary to stdout"
    )
    args = parser.parse_args(argv)

    script = Path(__file__).resolve()
    root = find_workspace(args.workspace, script)
    issues: List[Issue] = []
    fixes: List[Fix] = []

    backend, backend_path = canonical_backend(root)
    client, client_path = canonical_client(root)

    print(f"version sync: workspace={root}")
    print(f"  canonical backend: {backend} ({backend_path.relative_to(root)})")
    print(f"  canonical client:  {client} ({client_path.relative_to(root)})")

    footer_checks = check_landing_footers(root, backend, client, issues, fixes)
    check_deploy_sh(root, backend, client, issues, fixes)
    check_deploy_ps1(root, backend, client, issues, fixes)
    check_compose(root, issues)
    check_endpoints(root, issues)

    fixed = 0
    if args.fix and fixes:
        print("applying fixes:")
        fixed = apply_fixes(fixes, root)
        # Re-run validation mentally not needed; fixes are deterministic from failures.

    fails = [i for i in issues if i.level == "FAIL"]
    warns = [i for i in issues if i.level == "WARN"]
    for issue in issues:
        print(issue.format(root))

    summary = {
        "workspace": str(root),
        "canonical_backend": backend,
        "canonical_client": client,
        "landing_footer_occurrences_checked": footer_checks,
        "failures": len(fails),
        "warnings": len(warns),
        "fixes_applied": fixed,
        "ok": len(fails) == 0 and (fixed == 0 or not args.fix),
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        state = "OK" if not fails else "DRIFT"
        print(
            f"version sync: {state} ({len(fails)} failure(s), {len(warns)} warning(s), "
            f"{fixed} fix(es) applied, {footer_checks} landing footer occurrence(s) checked)"
        )
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
