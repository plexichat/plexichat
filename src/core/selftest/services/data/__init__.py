"""Self-test data generator package.

This package replaces the former single-file ``data.py``. The public surface
is unchanged: :class:`DataGenerator` is composed here from one mixin per
responsibility and re-exported under the original import path, so the runner
and the unit tests keep importing it the same way.
"""

from .bodies import MinimalBodyMixin
from .params import ParamValueMixin
from .schema import SchemaBodyMixin


class DataGenerator(
    ParamValueMixin,
    MinimalBodyMixin,
    SchemaBodyMixin,
):
    """Generates request bodies and resolves path/query parameters."""


__all__ = ["DataGenerator"]
