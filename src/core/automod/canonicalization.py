"""Content canonicalization for automod text matching.

Keyword blocklists, duplicate-spam detection and similar automod rules compare
user-controlled message text against admin-controlled patterns. Naive
``str.lower()`` / substring comparisons can be evaded without changing what a
human reads by:

  * splicing visually invisible format/control characters into a word
    (zero-width joiners/spaces, bidi overrides, soft hyphens, BOM, ...);
  * using fullwidth or other compatibility Unicode forms;
  * case variants that ``lower()`` does not fold (e.g. ``ß``).

Every comparison therefore folds both sides to a single canonical form: NFKC,
casefold (unless the rule is case-sensitive), removal of all invisible
format/control characters, collapsed whitespace and a hard length bound. This
mirrors the canonicalization already applied by the antispam detector so the
two moderation layers agree on what "the same text" means.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

# Cf = format (zero-width joiners, bidi overrides, direction marks, soft
# hyphen, BOM, ...); Cc = control (C0/C1 including NUL). Both are visually
# invisible yet change the bytes of a message, so removing the whole categories
# closes the class instead of maintaining a fixed codepoint denylist.
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc"})
_WS_RUN = re.compile(r"\s+")

# NFKC may expand one compatibility codepoint into many (U+FDFA -> 18). Bound
# the folded form so a short input cannot be amplified into an unbounded string
# for the linear substring scans in the rules.
MAX_CANONICAL_LENGTH = 10_000


def canonicalize_for_matching(
    content: Any,
    *,
    case_sensitive: bool = False,
    max_length: Optional[int] = MAX_CANONICAL_LENGTH,
) -> str:
    """Return the canonical form of ``content`` for automod matching.

    Both the message text and the blocklist entries must be passed through this
    function so they fold to identical bytes. When ``case_sensitive`` is true
    the case fold is skipped, but width folding and invisible-character removal
    still apply: those are normalization attacks, not case differences.

    ``max_length`` bounds the folded form for linear substring scans (NFKC can
    expand one compatibility codepoint into many, e.g. U+FDFA -> 18). Callers
    whose filter must see EVERY byte the consumer later delivers/renders -- and
    whose input is already length-bounded at the schema boundary -- should pass
    ``max_length=None`` to disable truncation. Truncating there would let a
    blocked token placed past the cut be matched as benign while the raw
    message containing it is delivered unchanged (the same class of bypass as
    the mass-mention one: filter view vs. consumer view disagree).
    """
    if content is None:
        return ""
    if not isinstance(content, str):
        content = str(content)
    text = unicodedata.normalize("NFKC", content)
    if not case_sensitive:
        text = text.casefold()
    text = "".join(
        ch for ch in text if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
    )
    # Strip invisibles BEFORE collapsing whitespace: an invisible next to a
    # space would otherwise leave a doubled whitespace run behind.
    text = _WS_RUN.sub(" ", text).strip()
    if max_length is not None and len(text) > max_length:
        text = text[:max_length]
    return text


def fold_invisible_for_matching(
    content: Any, *, max_length: Optional[int] = MAX_CANONICAL_LENGTH
) -> str:
    """NFKC-fold text and strip visually invisible characters, nothing else.

    ``canonicalize_for_matching`` also case-folds and collapses whitespace,
    which is right for substring/blocklist matching but wrong for rules that
    inspect the *shape* of the text: the repeated-character rule needs the exact
    run length, and the mass-emoji rule needs the exact character sequence to
    tell emoji from surrounding prose.

    Those rules previously matched the raw message, so a single zero-width
    separator (U+200B), soft hyphen, word joiner, bidi override or BOM spliced
    between repeated characters broke the run while rendering identically to a
    human -- obvious spam sailed through the filter. Folding both seeks and
    obfuscated forms to the same canonical bytes closes that gap.

    ``max_length`` mirrors ``canonicalize_for_matching``: callers whose filter
    must see EVERY byte the consumer later delivers/renders should pass
    ``max_length=None``. Truncating here can hide a run/emoji placed past the
    cut while the raw message is delivered unchanged (the filter/consumer view
    gap the canonicalization campaign closed for keywords and mentions).
    """
    if content is None:
        return ""
    if not isinstance(content, str):
        content = str(content)
    text = unicodedata.normalize("NFKC", content)
    text = "".join(
        ch for ch in text if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
    )
    if max_length is not None and len(text) > max_length:
        text = text[:max_length]
    return text
