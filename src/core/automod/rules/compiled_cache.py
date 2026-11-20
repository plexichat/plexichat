"""Bounded cache of compiled automod rule instances keyed by rule content.

Every message evaluation used to rebuild its rule objects from scratch::

    rule_instance = self.RULE_CLASSES[rule.rule_type](rule)

For ``RegexRule`` that recompiles every pattern (RE2 + stdlib fallback) and
re-runs the ``_has_redos_risk`` static analysis on every fallback pattern; for
``KeywordRule`` it re-canonicalizes every keyword; link/invite rules rebuild
their compiled patterns and normalized domain sets; caps/emoji rules re-read
and re-bound their config. None of that depends on the message being checked,
so repeating it per message is pure waste on the busiest path in the module.

The cache keys on the *content* that determines a compiled instance -- rule
identity, type, name, revision (``updated_at``), class and a stable rendering
of ``rule.config``. A fresh ``Rule`` row (carrying a new ``updated_at``) after
an admin edit therefore misses the cache and is recompiled, while unchanged
rows reuse the already-compiled instance. The bound keeps a server with a
rapidly churning rule set from growing the cache without limit.
"""

import json
import threading
from collections import OrderedDict
from typing import Any, Dict, Type

DEFAULT_MAX_ENTRIES = 256


def rule_content_key(rule: Any, rule_class: Type[Any]) -> tuple:
    """Return a hashable key derived entirely from a rule's compiled content.

    ``config`` is a JSON column, so a canonical (sorted-key) dump is a stable
    representation even when the dict was deserialized in a different order.
    ``default=str`` keeps exotic values hashable instead of raising; the
    fallback covers a config that cannot be rendered at all.
    """
    config = getattr(rule, "config", None)
    try:
        config_repr = json.dumps(
            config, sort_keys=True, separators=(",", ":"), default=str
        )
    except (TypeError, ValueError):
        config_repr = repr(config)
    return (
        getattr(rule, "id", None),
        getattr(rule, "rule_type", None),
        getattr(rule, "name", None),
        getattr(rule, "updated_at", None),
        rule_class,
        config_repr,
    )


class CompiledRuleCache:
    """Thread-safe, bounded LRU of compiled rule instances.

    Construction happens outside the lock so a slow compile (a large regex
    rule) never blocks lookups for unrelated rules. A duplicate construction
    racing on the same cold key is harmless: one instance is stored and both
    callers receive an equivalent, correct instance.
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("CompiledRuleCache max_entries must be at least 1")
        self._max_entries = int(max_entries)
        self._data: "OrderedDict[tuple, Any]" = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def get(self, rule: Any, rule_class: Type[Any]) -> Any:
        """Return the compiled instance for ``rule``, building it on a miss."""
        key = rule_content_key(rule, rule_class)
        with self._lock:
            cached = self._data.get(key)
            if cached is not None:
                self._data.move_to_end(key)
                self._hits += 1
                return cached

        instance = rule_class(rule)

        with self._lock:
            existing = self._data.get(key)
            if existing is not None:
                self._data.move_to_end(key)
                self._hits += 1
                return existing
            self._data[key] = instance
            self._data.move_to_end(key)
            self._misses += 1
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)
                self._evictions += 1
        return instance

    def clear(self) -> None:
        """Drop every compiled instance (called when global config reloads)."""
        with self._lock:
            self._data.clear()

    def stats(self) -> Dict[str, int]:
        """Snapshot of cache usage (useful for tests and diagnostics)."""
        with self._lock:
            return {
                "size": len(self._data),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
