#!/bin/sh
set -eu

apk add --no-cache bash gzip tar >/dev/null

mkdir -p /backups/postgres /backups/plexichat /backups/minio

while true; do
  TS="$(date +%Y%m%d_%H%M%S)"
  echo "[backup] running at ${TS}"

  export PGPASSWORD="${POSTGRES_PASSWORD}"
  pg_dump -h db -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER}" "${POSTGRES_DBNAME}" | gzip > "/backups/postgres/${TS}.sql.gz"

  tar -czf "/backups/plexichat/${TS}.tar.gz" -C / plexichat-home
  tar -czf "/backups/minio/${TS}.tar.gz" -C / minio-data

  find /backups/postgres -type f -mtime +30 -delete || true
  find /backups/plexichat -type f -mtime +30 -delete || true
  find /backups/minio -type f -mtime +30 -delete || true

  echo "[backup] done at ${TS}"
  sleep 86400
done
