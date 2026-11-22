"""Guard: automod/antispam filters must not block on a truncated canonical view.

Campaign context
----------------
``fold_invisible_for_matching`` / ``canonicalize_for_matching`` NFKC-fold user
text before automod matches it. Both truncate the folded form at
``MAX_CANONICAL_LENGTH`` (10_000) because NFKC can expand ONE codepoint into
many (U+FDFA -> 18 characters). That bound is safe for comparisons whose
consumer sees the *same truncated view*, but fatal for a block/detect decision
whose action consumes the *raw* message: a blocked token/mention/emoji/run
placed past the cut is invisible to the filter yet delivered verbatim.

Two real mismatches were fixed by peers:

* ``31911062`` (``MentionSpamRule``) - the notification fan-out parses mentions
  from RAW content, so counting only the truncated folded view let a user
  mass-ping past the cap. Fix: count the raw view too and take the max.
* ``c3b2c296`` (``KeywordRule``) - the send path stores/renders/copies the RAW
  message, so a blocked word past the cut was delivered unchanged. Fix: match
  the full canonical view (``max_length=None``).

This guard makes that review STRUCTURAL so a new rule cannot silently
reintroduce the class. It scans blobs from COMMITTED HEAD (never the working
tree: automod files are contested by parallel agents and a stale copy can
still contain an already-fixed pattern). For every call site of the two
canonicalizers in the automod/antispam trees it requires one of:

  (a) a full/untruncated view - ``max_length=None`` is passed explicitly, so
      the filter sees every byte the consumer later delivers; or
  (b) an entry in ``DOCUMENTED_TRUNCATED_VIEWS`` giving the concrete reason the
      truncation cannot open a filter/consumer gap (the consumer uses the SAME
      truncated view, or the raw view is independently covered).

Anything else fails closed. Self-tests prove the detector flags a synthetic
truncated-block-with-raw-consumer, accepts full-view calls, accepts a documented
same-view truncation, and flags an undocumented one. A non-vacuity test proves
the enumeration really reads committed rules and sees both full and truncated
views. The committed tree itself must have zero offenders.
"""

import ast
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]
SCAN_ROOTS = ("src/core/automod", "src/core/antispam")
CANONICALIZERS = frozenset(
    {"canonicalize_for_matching", "fold_invisible_for_matching"}
)

# (repo-relative path, exact ast.unparse of the call, reason).
# Each entry is a reviewed, PRE-EXISTING truncated view that is safe because the
# consumer uses the SAME view (or the raw view is independently covered). A NEW
# truncated block view must either pass max_length=None (case a) or be added
# here with an explicit reason; otherwise the guard fails closed.
DOCUMENTED_TRUNCATED_VIEWS: tuple[tuple[str, str, str], ...] = (
    (
        "src/core/automod/rules/keyword.py",
        "canonicalize_for_matching(keyword, case_sensitive=self._case_sensitive)",
        "Canonicalizes the ADMIN-CONFIGURED needle (bounded by MAX_KEYWORD_LENGTH "
        "at load time), not user message content. The user-content view is "
        "canonicalized separately with max_length=None in the same check(), so "
        "no raw-message block decision depends on this truncated form.",
    ),
    (
        "src/core/automod/rules/mentions.py",
        "fold_invisible_for_matching(content)",
        "The folded (truncated) view is only ONE of TWO views counted; the RAW "
        "content is also scanned by the same patterns and the per-bucket MAX is "
        "used for the block decision. Truncation therefore cannot hide a mention "
        "that the notification fan-out parses from raw text (fix 31911062).",
    ),
    (
        "src/core/automod/rules/spam.py",
        "canonicalize_for_matching(content)",
        "Duplicate spam compares canonical forms on BOTH sides: the current "
        "message here and the recent messages at the sibling call site (next "
        "entry) use the same truncation, so the consumer of this view is itself "
        "truncated identically. No raw-content block decision uses this form.",
    ),
    (
        "src/core/automod/rules/spam.py",
        "canonicalize_for_matching(msg.get('content', ''))",
        "Recent-message side of the duplicate comparison; truncated with the "
        "same canonicalizer and the same cap as the current-message view above. "
        "Both operands are the SAME truncated view, so comparison consistency "
        "is preserved and there is no raw-consumer gap.",
    ),
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run and pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _make_blob_reader(commit: str):
    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}")
        assert proc.returncode == 0, f"blob missing from {commit}: {path}"
        return proc.stdout

    return read


def _committed_files(commit: str) -> tuple[str, ...]:
    files: list[str] = []
    for root in SCAN_ROOTS:
        proc = _git("ls-tree", "-r", "--name-only", commit, "--", root)
        assert proc.returncode == 0, f"cannot list {root}: {proc.stderr.strip()}"
        files.extend(p for p in proc.stdout.splitlines() if p.endswith(".py"))
    assert files, f"no python modules found under {SCAN_ROOTS} in {commit}"
    return tuple(files)


def _canonical_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_full_view(call: ast.Call) -> bool:
    """True only when max_length=None is passed explicitly (no truncation)."""
    for kw in call.keywords:
        if kw.arg == "max_length":
            return (
                isinstance(kw.value, ast.Constant) and kw.value.value is None
            )
    return False


def _canonical_calls(tree: ast.AST) -> list[tuple[int, str, str, bool]]:
    """(lineno, function name, exact call source, is_full_view) per call site."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _canonical_name(node.func)
        if name in CANONICALIZERS:
            found.append(
                (node.lineno, name, ast.unparse(node), _is_full_view(node))
            )
    return found


def _all_calls(files, reader) -> list[tuple[str, int, str, str, bool]]:
    calls = []
    for path in files:
        tree = ast.parse(reader(path), filename=path)
        for lineno, name, source, full in _canonical_calls(tree):
            calls.append((path, lineno, name, source, full))
    return calls


def _offenders(
    calls, documented=DOCUMENTED_TRUNCATED_VIEWS
) -> list[str]:
    offenders = []
    for path, lineno, _name, source, full in calls:
        if full:
            continue
        if any(p == path and text == source for p, text, _why in documented):
            continue
        offenders.append(
            f"{path}:{lineno}: truncated canonical view on a filter/decision "
            f"with no full-view or same-view guarantee: {source}"
        )
    return offenders


# ---------------------------------------------------------------------------
# Self-tests: a detector that never fires must not let the real scan pass
# ---------------------------------------------------------------------------


def test_self_test_flags_truncated_block_view_with_raw_consumer():
    """The exact regression class must be flagged (both entry points)."""
    module = "src/core/automod/rules/_synthetic.py"
    sources = (
        '''
        def check(self, content):
            view = canonicalize_for_matching(content)
            if BAD_WORD in view:
                return self._block(content)  # action consumes RAW content
            return self._no_match()
        ''',
        '''
        def check(self, content):
            view = fold_invisible_for_matching(content)
            if "a" * 20 in view:
                return self._block(content)  # action consumes RAW content
            return self._no_match()
        ''',
    )
    for source in sources:
        calls = _all_calls((module,), lambda _p, s=source: textwrap.dedent(s))
        assert len(calls) == 1, source
        assert calls[0][4] is False, "synthetic call must be detected as truncated"
        assert _offenders(calls), f"truncated-block-with-raw-consumer not flagged: {source}"


def test_self_test_full_view_is_accepted():
    module = "src/core/automod/rules/_synthetic.py"
    source = """
    def check(self, content):
        a = canonicalize_for_matching(content, case_sensitive=False, max_length=None)
        b = fold_invisible_for_matching(content, max_length=None)
        return a, b
    """
    calls = _all_calls((module,), lambda _p: textwrap.dedent(source))
    assert len(calls) == 2
    assert all(c[4] for c in calls), "both calls must be recognized as full view"
    assert _offenders(calls) == []


def test_self_test_same_view_documented_accepted_but_new_flagged():
    """Documented same-view truncation is allowed; an undocumented one is not."""
    module = "src/core/automod/rules/_synthetic.py"
    source = """
    def _check_duplicate(self, content, recent):
        current = canonicalize_for_matching(content)
        for msg in recent:
            if canonicalize_for_matching(msg.get('content', '')) == current:
                return self._create_match(matched=True)
        return None
    """
    calls = _all_calls((module,), lambda _p: textwrap.dedent(source))
    assert len(calls) == 2
    assert all(not c[4] for c in calls)
    # Fail closed: a brand-new truncated block view is an offender.
    assert len(_offenders(calls)) == 2
    # A reviewed same-view reason admits exactly the two documented call sites.
    documented = (
        (
            module,
            "canonicalize_for_matching(content)",
            "both sides of the duplicate comparison use this same truncated view",
        ),
        (
            module,
            "canonicalize_for_matching(msg.get('content', ''))",
            "same-view operand of the duplicate comparison",
        ),
    )
    assert _offenders(calls, documented) == []


# ---------------------------------------------------------------------------
# Real committed tree
# ---------------------------------------------------------------------------


def test_scan_is_not_vacuous(commit_sha):
    files = _committed_files(commit_sha)
    for required in (
        "src/core/automod/rules/keyword.py",
        "src/core/automod/rules/mentions.py",
        "src/core/automod/rules/emoji.py",
        "src/core/automod/rules/repeated.py",
        "src/core/automod/rules/spam.py",
        "src/core/antispam/detector.py",
    ):
        assert required in files, f"{required} unexpectedly missing from scan"

    reader = _make_blob_reader(commit_sha)
    calls = _all_calls(files, reader)
    # Both canonicalizer entry points must actually be observed.
    assert any(c[2] == "canonicalize_for_matching" for c in calls)
    assert any(c[2] == "fold_invisible_for_matching" for c in calls)
    # At least one full-view call and at least one (documented) truncated call
    # must be present, otherwise the accept/reject branches are never exercised.
    assert any(c[4] for c in calls), "no full-view call found in committed tree"
    assert any(not c[4] for c in calls), "no truncated view found in committed tree"

    # The reader really returns COMMITTED content, not a stale working copy.
    keyword_src = reader("src/core/automod/rules/keyword.py")
    assert "max_length=None" in keyword_src
    assert ast.parse(keyword_src)


def test_no_undocumented_truncated_canonical_block_view(commit_sha):
    calls = _all_calls(
        _committed_files(commit_sha), _make_blob_reader(commit_sha)
    )
    offenders = _offenders(calls)
    assert not offenders, (
        "Automod/antispam filter blocks on a truncated canonical view whose "
        "consumer may use raw content (filter/consumer view gap):\n  "
        + "\n  ".join(sorted(offenders))
    )


# ---------------------------------------------------------------------------
# Behavioural proof that the two newly-found mismatches are actually closed
# ---------------------------------------------------------------------------


def _rule(rule_type, config):
    from src.core.automod.models import (
        ActionType,
        Rule,
        RuleAction,
    )

    return Rule(
        id=1,
        server_id=1,
        name="canonicalization view-consistency test",
        rule_type=rule_type,
        enabled=True,
        config=config,
        actions=[RuleAction(action_type=ActionType.DELETE_MESSAGE)],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=0,
    )


def test_emoji_rule_sees_emoji_past_nfkc_expansion_cap():
    """600x U+FDFA expands past the 10k cap; the emoji after it must still count."""
    from src.core.automod.models import RuleType
    from src.core.automod.rules.emoji import MassEmojiRule

    rule = MassEmojiRule(
        _rule(
            RuleType.MASS_EMOJI,
            {"max_emoji": 10, "max_percentage": 50.0, "min_length": 5},
        )
    )
    payload = "\ufdfa" * 600 + "\U0001f600" * 30
    assert rule.check(payload, 1, 1).matched is True


def test_repeated_rule_sees_run_past_nfkc_expansion_cap():
    """600x U+FDFA expands past the 10k cap; the 'a' run after it must still count."""
    from src.core.automod.models import RuleType
    from src.core.automod.rules.repeated import RepeatedCharsRule

    rule = RepeatedCharsRule(
        _rule(
            RuleType.REPEATED_CHARS,
            {"max_repeats": 5, "min_occurrences": 3},
        )
    )
    payload = "\ufdfa" * 600 + "a" * 20
    assert rule.check(payload, 1, 1).matched is True
