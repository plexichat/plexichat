#!/usr/bin/env python3
"""Fail-open authorization audit (re-runnable).

The findings annexes (O/P/Q) were produced by hand and went stale: by the time
they were re-checked, most listed items had already been remediated, so the
annex text no longer described the tree. This tool replaces that prose with an
executable sweep so the inventory can be regenerated on demand and gated in CI.

It looks for the single invariant class that caused every one of those bugs:

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

Three sweeps:

  A. ``src/core`` manager guards -- ``if not self._<dep>:`` whose block grants
     access (``return True``) instead of denying.
  B. ``src/api`` module guards -- ``if <module>:`` blocks that call an
     authorization helper (``has_permission``/``get_permissions``/
     ``require_permission``/``get_member``/``is_ip_blocked``) and have no
     denying branch.
  C. ``plexichat-client`` -- ``canEdit``-style gates that fail open on an
     unknown viewer or ignore the server's ``my_permissions`` projection.

It optionally cross-checks ``findings.md`` so the printed inventory states how
many recorded findings are still open versus annotated FIXED.

Usage:
    python ci-tooling/audit_failopen.py [--findings ../findings.md] [--root .]

Exit code 1 when any GRANT-class (fail-open) hit remains, 0 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, NamedTuple

# ---------------------------------------------------------------------------
# Classification vocabulary
# ---------------------------------------------------------------------------

GRANT = "GRANT"  # fails open: the guard skips the check when unwired -> bug
REVIEW = "REVIEW"  # needs human judgement (may be non-authoritative plumbing)
NON_AUTH = "NON-AUTH"  # bookkeeping / caching / logging, not an authz decision

# Cross-module dependencies: their absence means "cannot authorize".
DEP_ATTRS = {
    "_servers",
    "_servers_module",
    "_relationships",
    "_relationships_module",
    "_messaging",
    "_messaging_module",
    "_auth",
    "_auth_module",
    "_presence",
    "_presence_module",
    "_webhooks",
    "_threads",
    "_media",
    "_media_module",
    "_embeds",
    "_features",
    "_search",
    "_applications",
    "_notifications",
    "_stickers",
    "_soundboard",
    "_polls",
    "_settings",
    "_avatars",
    "_admin",
    "_telemetry",
    "_events",
    "_automod",
    "_moderation",
    "_ratelimit",
    "_valkey",
}

DEP_NAMES = {name.lstrip("_") for name in DEP_ATTRS} | {
    "servers_mod",
    "servers_module",
    "relationships_mod",
    "messaging_mod",
    "auth_mod",
    "presence_mod",
    "webhooks_mod",
    "threads_mod",
    "media_mod",
    "embeds_mod",
    "stickers_mod",
    "soundboard_mod",
    "polls_module",
    "settings_module",
    "applications_mod",
    "valkey",
    "redis",
}


def _is_dependency(symbol: str) -> bool:
    """True when the guarded name is a cross-module dependency.

    Local variables fetched from a query (``row``, ``stored``, ``file``,
    ``perms`` ...) are truthiness checks, not dependency guards, and must not
    be reported as fail-open authorization guards.
    """
    if symbol in DEP_ATTRS or symbol in DEP_NAMES:
        return True
    return False


# Authorization-oriented calls; a guard block that skips one of these without a
# denying branch is a candidate.
AUTHZ_CALLS = (
    "has_permission",
    "get_permissions",
    "require_permission",
    "get_member",
    "is_ip_blocked",
    "is_participant",
    "can_view",
    "check_admin_permission",
)

DENY_TOKENS = ("raise ", "return False", "return None", "deny", "forbidden")

GUARD_INVERTED = re.compile(r"^\s*if not (self\.)?(_?[A-Za-z_][A-Za-z0-9_]*)\s*:\s*$")
GUARD_NONE = re.compile(r"^\s*if (self\.)?(_?[A-Za-z_][A-Za-z0-9_]*) is None\s*:\s*$")
GUARD_POSITIVE = re.compile(r"^\s*if (self\.)?(_?[A-Za-z_][A-Za-z0-9_]*)\s*:\s*$")


class Hit(NamedTuple):
    kind: str
    path: str
    line: int
    symbol: str
    detail: str


def _block_after(lines: list[str], idx: int) -> list[str]:
    """Return the indented block following ``lines[idx]``."""
    head = lines[idx]
    indent = len(head) - len(head.lstrip())
    block: list[str] = []
    for follow in lines[idx + 1 :]:
        if not follow.strip():
            block.append(follow)
            continue
        if len(follow) - len(follow.lstrip()) <= indent:
            break
        block.append(follow)
    return block


def _has_deny(block: Iterable[str]) -> bool:
    text = "\n".join(block)
    return any(token in text for token in DENY_TOKENS)


def _has_grant(block: Iterable[str]) -> bool:
    return any("return True" in line for line in block)


def sweep_core(root: Path) -> list[Hit]:
    """Sweep A: manager guards in ``src/core`` that fail open."""
    hits: list[Hit] = []
    for path in sorted((root / "src" / "core").rglob("*.py")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for idx, line in enumerate(lines):
            for pattern in (GUARD_INVERTED, GUARD_NONE):
                match = pattern.match(line)
                if not match:
                    continue
                symbol = match.group(2)
                if not _is_dependency(symbol):
                    continue
                block = _block_after(lines, idx)
                if not block:
                    continue
                kind = GRANT if _has_grant(block) and not _has_deny(block) else NON_AUTH
                if kind == GRANT:
                    hits.append(
                        Hit(
                            kind,
                            str(path.relative_to(root)),
                            idx + 1,
                            symbol,
                            "guard block grants access instead of denying",
                        )
                    )
    return hits


def sweep_api(root: Path) -> list[Hit]:
    """Sweep B: API-layer guards that skip an authorization helper."""
    hits: list[Hit] = []
    for path in sorted((root / "src" / "api").rglob("*.py")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for idx, line in enumerate(lines):
            match = GUARD_POSITIVE.match(line)
            if not match:
                continue
            symbol = match.group(2)
            if not _is_dependency(symbol):
                continue
            block = _block_after(lines, idx)
            if not block:
                continue
            text = "\n".join(block)
            if not any(call in text for call in AUTHZ_CALLS):
                continue
            if _has_deny(block) and "raise" in text:
                # The guard body itself denies (optionally after best-effort
                # lookups), which is the fail-closed shape.
                continue
            hits.append(
                Hit(
                    REVIEW,
                    str(path.relative_to(root)),
                    idx + 1,
                    symbol,
                    "authorization helper inside an `if <module>:` guard "
                    "with no denying branch",
                )
            )
    return hits


# Function names that make an authorization decision on the client.
CLIENT_AUTH_FN = re.compile(
    r"(?:function|const|var|let)\s+"
    r"(can(?:Edit|Delete|Manage|View|Moderate|Post)|has(?:Permission|Access)|is(?:Owner|Admin|Moderator))"
    r"\s*[=(]"
)

# Tokens that identify a body as an access decision rather than a UI helper.
CLIENT_AUTH_TOKENS = (
    "permission",
    "author_id",
    "owner_id",
    "my_permissions",
    "can_edit",
    "role",
)

FAIL_OPEN = re.compile(r"if \(!\s*[\w.]*\w\s*\)\s*return true")
FAIL_CLOSED = re.compile(r"if \(!\s*[\w.]*\w\s*\)\s*return false")
INLINE_DELEGATE = re.compile(r"^\s*return\s+(ctx|window|this)\.\w+\(")


def sweep_client(client_root: Path) -> list[Hit]:
    """Sweep C: client access-decision helpers that fail open.

    Only bodies that actually decide access (they touch a permission, an
    ownership id, or the server ``my_permissions`` projection) are reported;
    generic UI helpers that happen to contain ``if (!x) return true`` (scroll
    heuristics, no-op checks) are not access decisions.
    """
    hits: list[Hit] = []
    js_root = client_root / "src" / "js"
    if not js_root.is_dir():
        return hits
    for path in sorted(js_root.rglob("*.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(client_root))
        for match in CLIENT_AUTH_FN.finditer(text):
            name = match.group(1)
            body = text[match.start() : match.start() + 1500]
            # A pure delegate to another implementation is not a decision site.
            if INLINE_DELEGATE.match(body.split(name, 1)[-1].split("\n", 1)[0]):
                continue
            if not any(token in body for token in CLIENT_AUTH_TOKENS):
                continue
            line_no = text[: match.start()].count("\n") + 1
            if FAIL_OPEN.search(body):
                hits.append(
                    Hit(
                        GRANT,
                        rel,
                        line_no,
                        name,
                        "unknown viewer is treated as authorized",
                    )
                )
            elif FAIL_CLOSED.search(body):
                hits.append(
                    Hit(
                        NON_AUTH,
                        rel,
                        line_no,
                        name,
                        "unknown viewer denied (fail closed)",
                    )
                )
            if "my_permissions" not in body:
                hits.append(
                    Hit(
                        REVIEW,
                        rel,
                        line_no,
                        name,
                        "does not consult the server my_permissions projection",
                    )
                )
    return hits


def findings_inventory(findings_path: Path) -> list[str]:
    """Summarise the recorded findings inventory, if present."""
    if not findings_path.is_file():
        return ["findings file not found: " + str(findings_path)]
    text = findings_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    status = [ln for ln in lines if ">> STATUS:" in ln]
    fixed = sum(1 for ln in status if re.search(r"STATUS:[^\n]*\bFIXED\b", ln))
    other = len(status) - fixed
    headers = [
        ln
        for ln in lines
        if re.match(r"^\[[CHML]\]", ln) or re.match(r"^\[[A-Z]\]", ln)
    ]
    annexes = [ln for ln in lines if ln.startswith("## ANNEX")]
    return [
        f"recorded findings with a STATUS line: {len(status)} "
        f"({fixed} FIXED, {other} other)",
        f"severity-tagged finding headers: {len(headers)}",
        f"annexes: {len(annexes)} (latest: {annexes[-1] if annexes else 'none'})",
    ]


def report(root: Path, client_root: Path, findings: Path) -> tuple[str, int]:
    hits = sweep_core(root) + sweep_api(root) + sweep_client(client_root)
    grants = [h for h in hits if h.kind == GRANT]
    reviews = [h for h in hits if h.kind == REVIEW]
    non_auth = [h for h in hits if h.kind == NON_AUTH]

    out: list[str] = []
    out.append("# Fresh fail-open authorization inventory")
    out.append("")
    out.append(
        "Generated by `plexichat/ci-tooling/audit_failopen.py`. Regenerate with:"
    )
    out.append("")
    out.append("```bash")
    out.append("python ci-tooling/audit_failopen.py --findings ../findings.md")
    out.append("```")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append(f"- GRANT (fail open, must fix): {len(grants)}")
    out.append(f"- REVIEW (needs a human call): {len(reviews)}")
    out.append(f"- NON-AUTH (verified denying/plumbing): {len(non_auth)}")
    out.append("")
    for line in findings_inventory(findings):
        out.append(f"- {line}")
    out.append("")

    for title, group in (
        ("GRANT - fails open", grants),
        ("REVIEW - needs a human call", reviews),
        ("NON-AUTH - verified fine", non_auth),
    ):
        out.append(f"## {title} ({len(group)})")
        out.append("")
        if not group:
            out.append("_(none)_")
            out.append("")
            continue
        out.append("| file:line | symbol | detail |")
        out.append("| --- | --- | --- |")
        for hit in group:
            out.append(f"| `{hit.path}:{hit.line}` | `{hit.symbol}` | {hit.detail} |")
        out.append("")

    return "\n".join(out), len(grants)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="plexichat server root")
    parser.add_argument(
        "--client-root",
        default="../plexichat-client",
        help="plexichat-client root (for sweep C)",
    )
    parser.add_argument(
        "--findings",
        default="../findings.md",
        help="findings.md path for the recorded-inventory summary",
    )
    parser.add_argument(
        "--output", default="-", help="write the report here instead of stdout"
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    client_root = (root / args.client_root).resolve()
    findings = (root / args.findings).resolve()

    text, grant_count = report(root, client_root, findings)
    if args.output == "-":
        print(text)
    else:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output} ({grant_count} GRANT hits)")
    return 1 if grant_count else 0


if __name__ == "__main__":
    sys.exit(main())
