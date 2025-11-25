import os
import sys

project_root = os.path.abspath(os.path.dirname(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")
if common_utils_path not in sys.path:
    sys.path.append(common_utils_path)

import utils.logger as logger
import utils.config as config
import utils.validator as validator

def main():
    """
    Main entry point for the PlexiChat application.
    Sets up all utilities with proper configuration.
    """
    
    default_config = {
        "logging": {
            "level": "DEBUG",
            "max_bytes": 10485760,
            "backup_count": 5,
            "zip_logs": True,
            "rotate": True
        },
        "database": {
            "type": "sqlite",
            "path": "data/plexichat.db",
            "postgres": {
                "host": "localhost",
                "port": 5432,
                "user": "postgres",
                "password": "",
                "dbname": "plexichat"
            },
            "connection_pool": {
                "min_connections": 1,
                "max_connections": 10
            }
        },
        "authentication": {
            "jwt": {
                "secret_key": "CHANGE_THIS_IN_PRODUCTION",
                "algorithm": "HS256",
                "access_token_expire_minutes": 30,
                "refresh_token_expire_days": 7
            },
            "password": {
                "min_length": 8,
                "require_uppercase": True,
                "require_lowercase": True,
                "require_digit": True,
                "require_special": True
            },
            "account_lockout": {
                "max_failed_attempts": 5,
                "lockout_duration_minutes": 15
            },
            "session": {
                "max_concurrent_sessions": 3
            }
        },
        "encryption": {
            "argon2": {
                "time_cost": 2,
                "memory_cost": 65536,
                "parallelism": 2,
                "hash_length": 32,
                "salt_length": 16
            },
            "aes_gcm": {
                "key_length": 32,
                "nonce_length": 12,
                "tag_length": 16
            },
            "snowflake": {
                "epoch": "2024-01-01T00:00:00Z",
                "worker_id": 1,
                "datacenter_id": 1
            }
        },
        "application": {
            "name": "PlexiChat",
            "version": "0.1.0",
            "environment": "development"
        }
    }
    
    config_path = os.path.join(project_root, "config", "config.yaml")
    config.setup(config_path=config_path, default_config=default_config)
    
    log_config = config.get("logging")
    log_dir = os.path.join(project_root, "logs")
    logger.setup(
        log_dir=log_dir,
        level=log_config.get("level", "INFO"),
        max_bytes=log_config.get("max_bytes", 10485760),
        backup_count=log_config.get("backup_count", 5),
        zip_logs=log_config.get("zip_logs", True),
        rotate=log_config.get("rotate", True)
    )
    
    validator.setup()
    
    logger.info("=" * 60)
    logger.info("PlexiChat Application Starting")
    logger.info("=" * 60)
    
    app_config = config.get("application")
    logger.info(f"Application: {app_config.get('name')}")
    logger.info(f"Version: {app_config.get('version')}")
    logger.info(f"Environment: {app_config.get('environment')}")
    
    logger.info("Configuration loaded successfully")
    logger.info(f"Config file: {config_path}")
    logger.info(f"Log directory: {log_dir}")
    
    db_config = config.get("database")
    logger.info(f"Database type: {db_config.get('type')}")
    if db_config.get('type') == 'sqlite':
        logger.info(f"Database path: {db_config.get('path')}")
    
    logger.info("All utilities initialized successfully")
    logger.info("=" * 60)
    
    return config, logger, validator

if __name__ == "__main__":
    config_obj, logger_obj, validator_obj = main()
    
    logger.info("Application ready")
    logger.info("Exiting main.py - application components are configured")
