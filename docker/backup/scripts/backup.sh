#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

COMMAND="${1:-help}"
shift 2>/dev/null || true

case "$COMMAND" in
  run)
    log_step "Starting backup run"
    START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    FAILED=0

    run_backup_to_repo "$RESTIC_REPOSITORY" "$RESTIC_PASSWORD" "primary" || FAILED=1

    if [ -n "$BACKUP_SECONDARY_REPO" ]; then
      log_step "Replicating to secondary repository..."
      run_backup_to_repo "$BACKUP_SECONDARY_REPO" "$BACKUP_SECONDARY_PASSWORD" "secondary" || FAILED=1
    fi

    if [ "$FAILED" -eq 0 ]; then
      log_info "Backup completed successfully"
      run_hook "$BACKUP_SUCCESS_HOOK" "success"
    else
      log_error "Backup completed with errors"
      run_hook "$BACKUP_FAILURE_HOOK" "failure"
    fi
    exit "$FAILED"
    ;;

  list)
    restic snapshots "$@"
    ;;

  restore)
    SNAPSHOT="${1:-latest}"
    TARGET="${2:-/restore}"
    shift 2 2>/dev/null || true
    log_info "Restoring snapshot $SNAPSHOT to $TARGET"
    exec restic restore "$SNAPSHOT" --target "$TARGET" "$@"
    ;;

  restore-files)
    SNAPSHOT="${1:-latest}"
    TARGET="${2:-/restore}"
    shift 2 2>/dev/null || true
    log_info "Restoring files from snapshot $SNAPSHOT to $TARGET"
    exec restic restore "$SNAPSHOT" --target "$TARGET" "$@"
    ;;

  restore-db)
    SNAPSHOT="${1:-latest}"
    log_info "Restoring database from snapshot $SNAPSHOT..."
    export PGPASSWORD="${POSTGRES_PASSWORD}"
    restic dump "$SNAPSHOT" /db_dump/plexichat.sql.gz 2>/dev/null \
      | gunzip \
      | psql -h db -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER}" -d "${POSTGRES_DBNAME}" --quiet
    log_info "Database restore complete"
    ;;

  stats)
    exec restic stats "$@"
    ;;

  init)
    exec restic init
    ;;

  check)
    exec restic check "$@"
    ;;

  unlock)
    exec restic unlock "$@"
    ;;

  help|--help|-h)
    cat << HELPEOF
Usage: backup.sh <command> [args]

Commands:
  run                    Run backup now (called by cron)
  list [opts]            List snapshots (restic snapshots)
  restore <snap> <dir>   Restore all files from snapshot
  restore-files <snap> <dir>  Restore files from snapshot
  restore-db <snap>      Restore database from snapshot
  stats [opts]           Show repository stats
  init                   Initialise restic repository
  check [opts]           Verify repository integrity
  unlock                 Remove stale locks

Environment:
  All config via env vars (see common.sh)
HELPEOF
    ;;

  *)
    log_error "Unknown command: $COMMAND"
    echo "Usage: backup.sh <command> [args]"
    echo "Run 'backup.sh help' for available commands"
    exit 1
    ;;
esac
