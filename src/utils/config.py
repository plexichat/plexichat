"""config utility - proxy to common_utils so ``utils.config`` resolves in the app."""

from .common_utils.utils.config import (
    ConfigLoader as ConfigLoader,
    MalformedConfigAction as MalformedConfigAction,
    setup as setup,
    get as get,
    set as set,
    get_all as get_all,
)
