# Query Processing

Query parsing, filtering, and ranking for search.

## Components

- `parser.py` - QueryParser for parsing search queries with operators
- `filters.py` - FilterProcessor for applying search filters
- `ranking.py` - RankingEngine for result relevance scoring

## Usage

```python
from src.core.search.query import parse_query, apply_filters, rank_results

parsed = parse_query("from:user has:image hello")
filtered = apply_filters(results, parsed.filters)
ranked = rank_results(filtered, parsed.query)
```
