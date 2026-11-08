#!/usr/bin/env bash
# Sample docker stats during a run. Usage: ./docker_stats_sample.sh 120 results/docker_stats.txt
set -euo pipefail
DUR="${1:-60}"; OUT="${2:-loadtest/results/docker_stats.txt}"; INT="${3:-2}"
mkdir -p "$(dirname "$OUT")"
echo "# docker stats sample start=$(date -Is) dur=${DUR}s interval=${INT}s" > "$OUT"
end=$((SECONDS + DUR))
while [ $SECONDS -lt $end ]; do
  echo "--- $(date -Is) ---" >> "$OUT"
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.PIDs}}' >> "$OUT" 2>&1 || true
  sleep "$INT"
done
echo "wrote $OUT"
