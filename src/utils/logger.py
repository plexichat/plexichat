"""logger utility - proxy to common_utils so ``utils.logger`` resolves in the app."""

from .common_utils.utils.logger import (
    Logger as Logger,
    DualFileHandler as DualFileHandler,
    setup as setup,
    debug as debug,
    info as info,
    warning as warning,
    error as error,
    critical as critical,
    get_logs as get_logs,
    get_current_log_path as get_current_log_path,
    get_latest_log_path as get_latest_log_path,
    sanitize_data as sanitize_data,
    sanitize_log_message as sanitize_log_message,
    mask_email as mask_email,
    mask_string as mask_string,
    _logger_instance as _logger_instance,
    _setup_called as _setup_called,
)
