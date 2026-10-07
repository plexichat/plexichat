"""The ``@cached`` L1 in-memory tier must not leak between tests.

``@cached`` keeps results in the module-global ``_mem_cache`` and keys them by
the decorated method's arguments (the bound instance is dropped so entries are
shared across instances), so an entry cached from one test's database is
visible to every later test in the same worker. The autouse
``_isolate_decorator_cache`` fixture clears that tier around each test; these
tests pin the isolation so it cannot be silently dropped. The failure it
prevents was order-dependent: a member cached by one sticker test was served
to the next test whose fresh database no longer had it.

The two tests MUST run in definition order: the first populates the key the
second asserts is gone.
"""

from src.core.database.cache.base import _mem_cache, mem_cache_set

_LEAK_KEY = "test_cache_isolation:leaked"


def test_first_the_l1_can_hold_a_value():
    mem_cache_set(_LEAK_KEY, {"leaked": True}, 60)
    assert _LEAK_KEY in _mem_cache


def test_second_the_l1_is_empty_at_the_start_of_the_next_test():
    assert _LEAK_KEY not in _mem_cache, (
        "a value cached by the previous test leaked into this one"
    )
