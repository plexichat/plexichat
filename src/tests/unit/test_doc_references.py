"""Docs cite `path.py:LINE`; keep every citation resolvable.

Line references drift as the code moves, and a citation that points past the
end of its file (or at a module that no longer exists) is worse than none: it
sends a reader to a file that cannot answer the question. This check is the
cheap half of the problem -- it cannot tell whether the line still shows the
symbol the sentence names, but it does catch deleted modules and line numbers
that the file has outgrown.
"""

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE = re.compile(r"([A-Za-z0-9_./-]+\.py):(\d+)")
DOC_GLOBS = ("README.md", "docs/**/*.md", "src/**/*.md")


def _documents():
    seen = {}
    for pattern in DOC_GLOBS:
        for path in PROJECT_ROOT.glob(pattern):
            seen[path.resolve()] = path
    return sorted(seen.values())


def _python_files():
    return [
        path
        for path in PROJECT_ROOT.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts
    ]


def _line_count(path):
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _resolve(reference, doc, python_files):
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
    matches = [path for path in python_files if path.as_posix().endswith(suffix)]
    if len(matches) == 1:
        return matches[0].resolve()
    return None


@pytest.mark.unit
def test_every_doc_line_reference_resolves():
    python_files = _python_files()
    problems = []

    for doc in _documents():
        relative_doc = doc.relative_to(PROJECT_ROOT)
        for lineno, line in enumerate(
            doc.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for match in REFERENCE.finditer(line):
                reference, cited_line = match.group(1), int(match.group(2))
                resolved = _resolve(reference, doc, python_files)
                if resolved is None:
                    problems.append(
                        f"{relative_doc}:{lineno}: {match.group(0)} names no file"
                    )
                    continue
                total = _line_count(resolved)
                if cited_line > total:
                    problems.append(
                        f"{relative_doc}:{lineno}: {match.group(0)} is past the end "
                        f"of {resolved.relative_to(PROJECT_ROOT)} ({total} lines)"
                    )

    assert not problems, "stale doc references:\n" + "\n".join(problems)
