"""Tests for the ``--shuffle-seed`` collection hook in ``_conftest.hooks``.

The hook exists so order-dependence is reproducible: a given seed must always
yield the same order, files must stay whole (so ``--dist loadfile`` still pins
a file to one xdist worker), and no collected item may be dropped or
duplicated.
"""

from types import SimpleNamespace

from src.tests._conftest.hooks import _item_file_key, shuffle_test_order


def _file_items(*files):
    """Three synthetic items per named file, in collection order."""
    return [
        SimpleNamespace(path=f"/tmp/{name}.py", name=f"test_{name}_{i}")
        for name in files
        for i in range(3)
    ]


def _order(items):
    return [(item.path, item.name) for item in items]


def test_same_seed_is_reproducible():
    items = _file_items("alpha", "beta", "gamma", "delta")
    assert _order(shuffle_test_order(items, 1234)) == _order(
        shuffle_test_order(items, 1234)
    )


def test_preserves_every_item_without_duplication():
    items = _file_items("alpha", "beta", "gamma")
    shuffled = shuffle_test_order(items, 7)
    assert sorted(_order(shuffled)) == sorted(_order(items))
    assert len(shuffled) == len(items)


def test_files_stay_adjacent_and_keep_relative_order():
    items = _file_items("alpha", "beta", "gamma", "delta", "epsilon")
    original = {}
    for item in items:
        original.setdefault(item.path, []).append(item.name)

    shuffled = shuffle_test_order(items, 99)
    positions = []
    seen = {}
    for position, item in enumerate(shuffled):
        seen.setdefault(item.path, []).append((position, item.name))

    assert set(seen) == set(original)
    for path, entries in seen.items():
        indices = [position for position, _ in entries]
        # Adjacent: a file's items occupy one contiguous run.
        assert indices == list(range(indices[0], indices[0] + len(indices)))
        # Relative order within the file is unchanged.
        assert [name for _, name in entries] == original[path]
        positions.extend(indices)

    assert sorted(positions) == list(range(len(items)))


def test_distinct_seeds_can_produce_distinct_orders():
    items = _file_items("alpha", "beta", "gamma", "delta")
    orders = {tuple(_order(shuffle_test_order(items, seed))) for seed in range(25)}
    assert len(orders) > 1


def test_single_file_order_is_unchanged():
    items = _file_items("alpha")
    assert _order(shuffle_test_order(items, 5)) == _order(items)


def test_falls_back_to_fspath_when_path_is_absent():
    item = SimpleNamespace(fspath="/tmp/legacy.py")
    assert _item_file_key(item) == "/tmp/legacy.py"
