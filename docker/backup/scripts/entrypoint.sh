#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/common.sh"

if [ $# -gt 0 ]; then
  exec "$@"
fi

log_info "Initialising restic repository if needed..."
ensure_repo

log_info "Setting up backup schedule: $BACKUP_SCHEDULE"
echo "$BACKUP_SCHEDULE /scripts/backup.sh run" > /etc/crontab

log_info "Starting supercronic..."
exec supercronic /etc/crontab
