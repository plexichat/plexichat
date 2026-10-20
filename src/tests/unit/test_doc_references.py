"""Docs cite `path:LINE`; keep every citation resolvable.

Line references drift as the code moves, and a citation that points past the
end of its file (or at a path that no longer exists) is worse than none: it
sends a reader to a file that cannot answer the question. This check is the
cheap half of the problem -- it cannot tell whether the line still shows the
symbol the sentence names, but it does catch deleted files and line numbers
that the file has outgrown.

The deployment pages cite ``docker-compose.yml``, the nginx config and the
``Dockerfile`` by line as often as they cite Python modules, and those drift
with the same silence, so the scan covers every file type the docs cite rather
than ``.py`` alone. The extension list is deliberately closed: ``host:port``
literals (``127.0.0.1:8000``, ``stun.l.google.com:19302``) are everywhere in
these docs and are not citations.
"""

import os
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# ``path/to/file:LINE``, plus the two extensionless files the docs name.
REFERENCE = re.compile(
    r"([A-Za-z0-9_./-]+\."
    r"(?:py|ya?ml|json|toml|ini|conf|sh|md|ts|js|mjs|cjs|css|html)):(\d+)"
    r"|\b(Dockerfile|Makefile):(\d+)"
)
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
DOC_GLOBS = ("README.md", "docs/**/*.md", "src/**/*.md")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "<", "/")

# The client is a separate repository with its own checkout and its own guard,
# so a citation into it cannot be resolved from here -- not in CI, where only
# this repository is cloned, and not for a reader browsing this tree.
SIBLING_PREFIXES = ("plexichat-client/",)

# ``config/`` is gitignored: the deploy script writes the runtime config there
# at install time, so a citation into it has nothing to resolve in a fresh
# checkout or in CI. The docs cite it as an example of what the install
# produces, not as a tracked source.
GENERATED_PREFIXES = ("config/",)
UNCHECKABLE_PREFIXES = SIBLING_PREFIXES + GENERATED_PREFIXES

# Directories that hold data or build output rather than citable sources. The
# suffix fallback below walks the tree, and the uploaded media is not a doc
# target.
SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "data",
    "uploads",
    "test_artifacts",
}


def _documents():
    seen = {}
    for pattern in DOC_GLOBS:
        for path in PROJECT_ROOT.glob(pattern):
            seen[path.resolve()] = path
    return sorted(seen.values())


def _project_files():
    """Every file the suffix fallback may match, without walking skipped dirs.

    ``rglob`` would descend into ``.venv`` (thousands of files, and the one
    reason this scan once took 4.6s -- close enough to the slow-test gate's
    default to red it on a loaded runner) before the filter could drop it, so
    the walk prunes instead of filtering after the fact.
    """
    files = []
    for dirpath, dirnames, filenames in os.walk(PROJECT_ROOT):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        files.extend(Path(dirpath, name) for name in filenames)
    return files


def _citations(line):
    """Yield ``(as_written, path, line_number)`` for every citation on a line."""
    for match in REFERENCE.finditer(line):
        if match.group(1):
            yield match.group(0), match.group(1), int(match.group(2))
        else:
            yield match.group(0), match.group(3), int(match.group(4))


def _line_count(path):
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _resolve(reference, doc, project_files):
    """Resolve a citation the way a reader would: from the doc, then the root."""
    target = reference.removeprefix("plexichat/")
    for candidate in (
        doc.parent / target,
        PROJECT_ROOT / target,
        PROJECT_ROOT / "src" / target,
    ):
        if candidate.is_file():
            return candidate.resolve()

    # Last resort for a citation written against a sibling module: accept it
    # only when exactly one file in the tree carries that suffix.
    suffix = "/" + target
    matches = [path for path in project_files if path.as_posix().endswith(suffix)]
    if len(matches) == 1:
        return matches[0].resolve()
    return None


@pytest.mark.unit
def test_every_doc_line_reference_resolves():
    project_files = _project_files()
    problems = []

    for doc in _documents():
        relative_doc = doc.relative_to(PROJECT_ROOT)
        for lineno, line in enumerate(
            doc.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for written, reference, cited_line in _citations(line):
                if reference.removeprefix("plexichat/").startswith(
                    UNCHECKABLE_PREFIXES
                ):
                    continue
                resolved = _resolve(reference, doc, project_files)
                if resolved is None:
                    problems.append(f"{relative_doc}:{lineno}: {written} names no file")
                    continue
                total = _line_count(resolved)
                if cited_line > total:
                    problems.append(
                        f"{relative_doc}:{lineno}: {written} is past the end "
                        f"of {resolved.relative_to(PROJECT_ROOT)} ({total} lines)"
                    )

    assert not problems, "stale doc references:\n" + "\n".join(problems)


@pytest.mark.unit
def test_every_doc_link_resolves():
    """A relative markdown link must point at a file that exists.

    Docs move; a link into the old location is a dead end for a reader and
    invisible to every other check.
    """
    problems = []
    for doc in _documents():
        relative_doc = doc.relative_to(PROJECT_ROOT)
        for lineno, line in enumerate(
            doc.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for match in LINK.finditer(line):
                target = match.group(1).split("#", 1)[0].strip()
                if not target or target.startswith(EXTERNAL_PREFIXES):
                    continue
                if not (doc.parent / target).resolve().exists():
                    problems.append(f"{relative_doc}:{lineno}: broken link to {target}")

    assert not problems, "broken doc links:\n" + "\n".join(problems)
