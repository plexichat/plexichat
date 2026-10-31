#!/usr/bin/env python3
"""Fail when a docs page links somewhere the docs router does not serve.

The pages are markdown in the repository, but the router serves flat routes
(`deployment/configuration/config-api.md` -> `/config-api`). When a link has no
served target the renderer deliberately keeps the words and drops the link, so
a broken cross-reference is invisible in the HTML: the page still renders, it
just stops being navigable. Two whole admin guides were published this way --
linked from docs/admin/index.md, served by nobody.

This walks every page, resolving links exactly as the renderer does (including
the page's own directory, which is what sibling links like `rbac.md` need),
and fails on any in-repository link that resolves to nothing.

Run from the repository root:  python ci-tooling/check_docs_links.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

# The docs renderer transitively imports the rate-limit config, which refuses to
# load before the app config is set up.
import utils.config as app_config  # noqa: E402
import utils.version as app_version  # noqa: E402

_tmpdir = tempfile.mkdtemp()
app_config.setup(
    config_path=os.path.join(_tmpdir, "config.yaml"),
    default_config={"version": {"current": "r.1.0-1", "min_client": "a.1.0-1"}},
)
app_version.setup(current_version="r.1.0-1", min_supported_version="a.1.0-1")

from src.api.routes.docs.config import DocsConfig  # noqa: E402
from src.api.routes.docs.navigation import build_sidebar_html  # noqa: E402
from src.api.routes.docs.renderer import (  # noqa: E402
    LINK_RE,
    MISTUNE_AVAILABLE,
    _known_doc_routes,
    resolve_doc_link,
)

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#")


def iter_links(line: str):
    for match in LINK_RE.finditer(line):
        yield match.group(1), match.group(2)


def markdown_links(text: str):
    """Yield (line_number, label, url) for links outside fenced code."""
    in_fence = False
    for number, line in enumerate(text.split("\n"), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence or "](" not in line:
            continue
        for label, url in iter_links(line):
            yield number, label, url


def check_page(
    path: Path, routes: set[str], conf: DocsConfig, failures: list[str]
) -> int:
    text = path.read_text(encoding="utf-8")
    try:
        source_dir = path.parent.relative_to(REPO_ROOT / "docs").as_posix()
    except ValueError:
        source_dir = ""
    if source_dir == ".":
        source_dir = ""

    prefix = conf.path.rstrip("/")
    checked = 0
    for number, label, url in markdown_links(text):
        checked += 1
        if url.startswith(EXTERNAL_PREFIXES):
            continue
        # A link that already names its served path (".../docs/api/x") is
        # resolved on the part after the docs prefix, exactly as the renderer
        # does before re-prefixing it.
        target = (
            url[len(prefix) + 1 :] if prefix and url.startswith(prefix + "/") else url
        )
        resolved = resolve_doc_link(target, routes, source_dir)
        if resolved is None:
            failures.append(
                f"{path.relative_to(REPO_ROOT)}:{number} "
                f"[{label}]({url}) resolves to no served route"
            )
    return checked


def check_sidebar(routes: set[str], conf: DocsConfig, failures: list[str]) -> int:
    html = build_sidebar_html(conf, "/")
    prefix = conf.path.rstrip("/")
    checked = 0
    for href in sorted(set(re.findall(r'href="([^"]+)"', html))):
        if not (href == prefix or href.startswith(prefix + "/")):
            continue
        checked += 1
        path = href[len(prefix) :].split("#")[0].rstrip("/") or "/"
        if path not in routes:
            failures.append(f"sidebar: {href} is not a served route")
    return checked


def main() -> int:
    if not MISTUNE_AVAILABLE:
        print("FAIL: mistune is not importable; every page would silently render")
        print("      through the fallback renderer. Fix the import, not this check.")
        return 1

    routes = _known_doc_routes()
    if not routes:
        print("FAIL: the docs router registered no routes.")
        return 1

    conf = DocsConfig()
    failures: list[str] = []
    pages = 0
    links = 0
    for path in sorted((REPO_ROOT / "docs").rglob("*.md")):
        pages += 1
        links += check_page(path, routes, conf, failures)
    links += check_sidebar(routes, conf, failures)

    print(f"docs link check: {pages} pages, {links} links, {len(routes)} routes")
    if failures:
        for failure in failures:
            print(f"  FAIL {failure}")
        print(f"{len(failures)} unresolvable link(s): a published page is unreachable.")
        return 1
    print("  OK: every in-repository docs link resolves to a served route.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
