import sys
import logging


def handle_migrate_kek(args) -> None:
    from src.utils.encryption.kek_migration import (
        validate_keyrings,
        migrate_keyring,
        migrate_all_keyrings,
        rollback_keyring,
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    if args.kek_validate:
        if args.kek_all:
            success = validate_keyrings(all_keyrings=True)
        else:
            success = validate_keyrings(all_keyrings=False)
        sys.exit(0 if success else 1)

    if args.kek_rollback:
        if not args.kek_keyring:
            print("Error: --kek-rollback requires --kek-keyring")
            sys.exit(1)
        success = rollback_keyring(args.kek_keyring)
        sys.exit(0 if success else 1)

    if args.kek_all:
        if not args.kek_new_env:
            print("Error: --kek-all requires --kek-new-env")
            sys.exit(1)
        success = migrate_all_keyrings(
            args.kek_new_env, args.kek_force, args.kek_dry_run
        )
        sys.exit(0 if success else 1)

    if args.kek_keyring:
        if not args.kek_old_env or not args.kek_new_env:
            print("Error: --kek-keyring requires both --kek-old-env and --kek-new-env")
            sys.exit(1)
        success = migrate_keyring(
            args.kek_keyring,
            args.kek_old_env,
            args.kek_new_env,
            args.kek_force,
            args.kek_dry_run,
        )
        sys.exit(0 if success else 1)

    print(
        "Error: Invalid KEK migration arguments. Use --kek-validate, --kek-rollback, --kek-all, or --kek-keyring"
    )
    sys.exit(1)
