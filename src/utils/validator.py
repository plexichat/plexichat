"""validator utility - proxy to common_utils so ``utils.validator`` resolves in the app."""

from .common_utils.utils.validator import (
    Validator as Validator,
    ValidationResult as ValidationResult,
    setup as setup,
    validate as validate,
    add_pattern as add_pattern,
)
