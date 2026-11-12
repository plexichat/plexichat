"""Shared state for the self-test data generator mixins.

``DataGenerator`` is composed from one mixin per responsibility. This module
owns the context handle and the snowflake generator, and declares the
schema-to-body entry point the body mixin calls across modules.
"""

import secrets
from typing import Any, Dict, Optional

from ...context import SelfTestContext


class DataGeneratorBase:
    """Context handle plus the cross-mixin method contract."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def generate_snowflake(self) -> int:
        return secrets.randbits(60) + 10**17

    # -- Implemented by the behaviour mixins ------------------------------
    def generate_from_schema(
        self, schema: Dict[str, Any], prop_name: Optional[str] = None
    ) -> Any:
        raise NotImplementedError
