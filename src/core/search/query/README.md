# Query processing

This directory parses, filters, and ranks search queries before results are returned to the caller.

## Overview

A raw query such as `from:alice has:image "exact phrase" hello` passes through three stages:

1. **Parse** — `parser.py:45` extracts search terms, exact phrases, and typed filters.
2. **Filter** — `filters.py:13` applies those filters to an intermediate result set.
3. **Rank** — `ranking.py:31` scores and sorts the remaining results.

You can use each stage independently or through the convenience functions.

## Components

### Parser — `parser.py:45`

`QueryParser` converts a raw string into a `ParsedQuery` (`models.py:53`) with `search_terms`, `filters`, and `exact_phrases`.

Supported filters (`parser.py:24`):

| Filter | Example | Description |
|--------|---------|-------------|
| `from:` | `from:alice` or `from:me` | Messages from a user. `me` and `@me` resolve to the caller. |
| `in:` | `in:#general` | Messages in a channel. A leading `#` is stripped. |
| `before:` | `before:2024-01-01` or `before:7d` | Messages before a date. Relative units: `d`, `w`, `m`, `y`. Also `today` and `yesterday`. |
| `after:` | `after:2024-01-01` | Messages after a date. Same date formats as `before:`. |
| `has:` | `has:image`, `has:link`, `has:file` | Messages with an attachment type. Valid values: `link`, `image`, `file`, `embed`, `video`, `audio`, `attachment` (`parser.py:35`). |
| `mentions:` | `mentions:bob` | Messages that mention a user. |
| `pinned:` | `pinned:true` | Pinned messages. Accepts `true`/`yes`/`1` and `false`/`no`/`0`. |
| `reaction:` | `reaction:👍` | Messages with a specific reaction. |
| Negation | `-from:bob` | Prefix any filter with `-` to exclude matches. |
| Exact phrase | `"hello world"` | Text inside double quotes must appear verbatim. |

`get_filter_suggestions()` in `parser.py:176` returns autocomplete candidates for a partial token such as `has:`.

### Filter processor — `filters.py:13`

`FilterProcessor` applies a `ParsedQuery` to a list of `MessageSearchResult` objects for a specific `user_id`.

* Each filter in `ParsedQuery.filters` is applied sequentially via `_apply_single_filter()` (`filters.py:57`).
* Negated filters invert the predicate.
* Exact phrases are matched case-insensitively against `result.content` (`filters.py:52`).
* User and channel identifiers are resolved through `_resolve_user()` (`filters.py:148`) and `_resolve_channel()` (`filters.py:187`), which check an in-memory `CappedDict` cache and then fall back to `auth_users` and `srv_channels` tables.
* Dates are normalized to `YYYY-MM-DD` and converted to millisecond timestamps (`filters.py:224`).

### Ranking engine — `ranking.py:31`

`RankingEngine` scores results so that more relevant items appear first. Override weights with `RankingWeights` (`ranking.py:19`):

| Weight | Default | Effect |
|--------|---------|--------|
| `text_relevance` | `1.0` | Base FTS rank |
| `recency` | `0.3` | Newer messages score higher (log-decayed by age in hours) |
| `exact_phrase` | `2.0` | Bonus per matched exact phrase |
| `pinned` | `0.2` | Bonus for pinned messages |
| `attachment` | `0.1` | Bonus for messages with attachments |

Additional signals:

* **Users** (`ranking.py:144`) — exact username match (+5), prefix match (+3), substring (+1); similar bonuses for display name; mutual servers and friends add up to +2 and +3 respectively.
* **Servers** (`ranking.py:178`) — name and description matches, tag matches, member count (log-scaled), and verified status.

## Usage

Parse a query:

```python
from plexichat.src.core.search.query import QueryParser

parser = QueryParser()
parsed = parser.parse('from:alice has:image "hello world" -pinned:false')
print(parsed.search_terms)    # ['hello', 'world'] or similar
print(parsed.exact_phrases)   # ['hello world']
print(parsed.filters)         # [QueryFilter(...), ...]
```

Filter results for a specific user:

```python
from plexichat.src.core.search.query import FilterProcessor

processor = FilterProcessor(db, auth_module, servers_module)
filtered = processor.apply_filters(results, parsed, user_id=123)
```

Rank results:

```python
from plexichat.src.core.search.query import RankingEngine

engine = RankingEngine()
ranked = engine.rank_message_results(filtered, parsed, now_ms=timestamp_ms())
```

Use the convenience functions for one-off operations:

```python
from plexichat.src.core.search.query import parse_query, apply_filters, rank_results

parsed = parse_query("from:me has:link hello")
filtered = apply_filters(results, parsed, user_id=123, db=db)
ranked = rank_results(filtered, parsed_query=parsed, now_ms=timestamp_ms())
```

`rank_results()` in `ranking.py:213` dispatches automatically based on the result type (messages, users, or servers).
