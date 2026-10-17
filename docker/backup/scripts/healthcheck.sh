#!/bin/sh
# Backup service liveness check.
#
# Healthy when ALL of the following hold:
#   1. supercronic is actually running;
#   2. a successful-backup marker exists and is fresh (< 48h), OR the
#      container is still within its 48h grace window before the first
#      scheduled run.
#
# This catches: dead cron loops, empty restic passwords, failed mounts and
# backup runs that exit non-zero - none of which were previously detected.
set -eu

NOW=$(date -u +%s)
START=$(cat /tmp/.container_start 2>/dev/null || echo "$NOW")

pgrep -f 'supercronic /etc/crontab' >/dev/null || exit 1

if [ -f /tmp/.last_successful_backup ]; then
    LAST=$(cat /tmp/.last_successful_backup)
    [ "$((NOW - LAST))" -le 172800 ] || exit 1
else
    # No run yet - allow the grace period, then require a marker.
    [ "$((NOW - START))" -le 172800 ] || exit 1
fi

exit 0
