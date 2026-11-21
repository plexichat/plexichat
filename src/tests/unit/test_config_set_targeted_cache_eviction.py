"""Regression: a single ``config.set()`` must evict only the mutated key's
cached ``get()`` entries instead of flushing the whole hot-path cache.

``ConfigLoader.get()`` is called across the app on the request hot path, and
admin handlers (e.g. the automod settings write) mutate one config key at a
time. Clearing every cached key on such a mutation threw away warm lookups for
all unrelated keys. This module pins the targeted-eviction behaviour.
"""

import os

from utils.config import ConfigLoader


def _loader(tmp_path):
    return ConfigLoader(
        os.path.join(str(tmp_path), "config.yaml"),
        default_config={
            "db_host": "localhost",
            "media": {},
            "search": {},
            "automod": {},
        },
    )


def test_set_evicts_only_mutated_key(tmp_path):
    loader = _loader(tmp_path)
    for key, default in (
        ("db_host", None),
        ("media", {}),
        ("search", {}),
        ("automod", {}),
    ):
        loader.get(key, default)

    assert len(loader._get_cache) == 4

    loader.set("automod", {"enabled": True})

    surviving = {ck[0] for ck in loader._get_cache}
    assert "automod" not in surviving, "mutated key must be evicted"
    assert surviving == {"db_host", "media", "search"}, (
        "unrelated hot-path lookups must survive a single-key mutation"
    )


def test_set_refreshes_mutated_key(tmp_path):
    loader = _loader(tmp_path)
    assert loader.get("db_host") == "localhost"
    loader.set("db_host", "newhost")
    assert loader.get("db_host") == "newhost", "new value must be visible"


def test_set_preserves_multiple_default_variants_of_other_keys(tmp_path):
    loader = _loader(tmp_path)
    loader.get("media", {})
    loader.get("media", {"fallback": True})
    loader.get("automod", {})

    loader.set("automod", {"enabled": True})

    assert {ck[0] for ck in loader._get_cache} == {"media"}, (
        "all default-variants of unrelated keys must remain cached"
    )


def test_reload_still_clears_every_key(tmp_path):
    loader = _loader(tmp_path)
    loader.get("db_host")
    loader.get("media")

    loader._load_config()

    assert loader._get_cache == {}, "a full reload must still flush all entries"
