"""Base class for SetupService mixins.

Declares self.ctx with a type annotation so pyright can resolve
attribute access across all mixin files without suppression comments.
"""

from ...context import SelfTestContext


class SetupServiceBase:
    """Base class providing typed access to the shared SelfTestContext."""

    ctx: SelfTestContext

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx
