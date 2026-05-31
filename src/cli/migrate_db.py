import os
import sys

import utils.logger as logger
import utils.config as config


def handle_migrate_db(project_root: str, dry_run: bool = False) -> None:
    from src.core.database import Database
    from src.core.migrations import run_migrations, get_status

    config.setup(config_path=os.path.join(project_root, "config", "config.yaml"))

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

    db = Database()
    db.connect()

    status = get_status(db)
    print(
        f"Migration Status: {status['applied_count']} applied, {status['pending_count']} pending"
    )

    if dry_run:
        print("\nDRY RUN — no changes will be made.\n")

    result = run_migrations(db, dry_run=dry_run)
    if result["success"]:
        if result.get("dry_run"):
            print(
                f"\nDry run complete — {result['applied_count']} migrations "
                f"would be applied (no changes made)."
            )
        else:
            print(f"Migrations applied successfully: {result['applied_count']} applied")
        sys.exit(0)
    else:
        print(f"Migration process had failures: {result['failed_count']} failed")
        sys.exit(1)
