#!/bin/bash
set -euo pipefail

export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-/repo}"
export RESTIC_PASSWORD="${RESTIC_PASSWORD:-}"
RESTIC_HOST="${RESTIC_HOST:-plexichat}"
RESTIC_TAG="${RESTIC_TAG:-production}"

BACKUP_SCHEDULE="${BACKUP_SCHEDULE:-0 2 * * *}"
BACKUP_KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
BACKUP_KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"
BACKUP_KEEP_MONTHLY="${BACKUP_KEEP_MONTHLY:-6}"
BACKUP_KEEP_YEARLY="${BACKUP_KEEP_YEARLY:-0}"

BACKUP_INCLUDE_DB="${BACKUP_INCLUDE_DB:-true}"
BACKUP_INCLUDE_HOME="${BACKUP_INCLUDE_HOME:-true}"
BACKUP_INCLUDE_MINIO="${BACKUP_INCLUDE_MINIO:-true}"

BACKUP_SECONDARY_REPO="${BACKUP_SECONDARY_REPO:-}"
BACKUP_SECONDARY_PASSWORD="${BACKUP_SECONDARY_PASSWORD:-}"

BACKUP_DB_DUMP_PATH="${BACKUP_DB_DUMP_PATH:-/tmp/backup/db}"

BACKUP_SUCCESS_HOOK="${BACKUP_SUCCESS_HOOK:-}"
BACKUP_FAILURE_HOOK="${BACKUP_FAILURE_HOOK:-}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { printf "${GREEN}[INFO]${NC}  %s\n" "$*"; }
log_warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
log_error() { printf "${RED}[ERROR]${NC} %s\n" "$*"; }
log_step()  { printf "${CYAN}[STEP]${NC}  %s\n" "$*"; }

ensure_repo() {
  if ! restic snapshots --quiet 2>/dev/null; then
    log_info "Initialising new restic repository at $RESTIC_REPOSITORY"
    restic init
  fi
}

run_hook() {
  local hook="$1"
  local status="$2"
  if [ -n "$hook" ]; then
    HOOK_STATUS="$status" HOOK_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)" eval "$hook" || log_warn "Hook exited non-zero: $hook"
  fi
}

do_retention() {
  local repo="$1"
  local password="$2"
  RESTIC_REPOSITORY="$repo" RESTIC_PASSWORD="$password" restic forget \
    --keep-daily "$BACKUP_KEEP_DAILY" \
    --keep-weekly "$BACKUP_KEEP_WEEKLY" \
    --keep-monthly "$BACKUP_KEEP_MONTHLY" \
    --keep-yearly "$BACKUP_KEEP_YEARLY" \
    --tag "$RESTIC_TAG" \
    --prune
}

run_backup_to_repo() {
  local repo="$1"
  local password="$2"
  local label="$3"

  export RESTIC_REPOSITORY="$repo"
  export RESTIC_PASSWORD="$password"

  ensure_repo

  RESTIC_ARGS=(--host "$RESTIC_HOST" --tag "$RESTIC_TAG")

  if [ "$BACKUP_INCLUDE_DB" = "true" ]; then
    log_info "Dumping PostgreSQL database..."
    mkdir -p "$BACKUP_DB_DUMP_PATH"
    export PGPASSWORD="${POSTGRES_PASSWORD}"
    pg_dump -h db -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER}" "${POSTGRES_DBNAME}" \
      | gzip > "$BACKUP_DB_DUMP_PATH/plexichat.sql.gz"
    RESTIC_ARGS+=("$BACKUP_DB_DUMP_PATH")
  fi

  if [ "$BACKUP_INCLUDE_HOME" = "true" ]; then
    RESTIC_ARGS+=(/plexichat-home)
  fi

  if [ "$BACKUP_INCLUDE_MINIO" = "true" ]; then
    RESTIC_ARGS+=(/minio-data)
  fi

  log_info "Running restic backup to $label..."
  restic backup "${RESTIC_ARGS[@]}"

  if [ -n "$BACKUP_DB_DUMP_PATH" ] && [ -d "$BACKUP_DB_DUMP_PATH" ]; then
    rm -rf "$BACKUP_DB_DUMP_PATH"
  fi

  do_retention "$repo" "$password"
}
