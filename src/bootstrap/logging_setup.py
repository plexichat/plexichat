import os

import utils.logger as logger
import utils.config as config


def setup_logging(project_root: str) -> None:
    log_config = config.get("logging", {})
    media_config = config.get("media", {})

    log_dir = media_config.get("logs_dir", os.path.join(project_root, "logs"))

    log_dir = os.path.expanduser(log_dir)

    logger.setup(
        log_dir=log_dir,
        level=log_config.get("level", "INFO"),
        max_bytes=log_config.get("max_bytes", 10485760),
        backup_count=log_config.get("backup_count", 5),
        zip_logs=log_config.get("zip_logs", True),
        rotate=log_config.get("rotate", True),
    )
