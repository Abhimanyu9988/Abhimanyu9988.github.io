#!/usr/bin/env bash
# THE FIX: keep the migration (no batch processor, sending_queue batch enabled) and size the queue in items, as the
# OpenTelemetry blog suggests: sizer items, queue_size 100000. Runs sized-1 and sized-2 under the same load and outage, then
# reads from Splunk APM how many spans of the load service it holds for each of the six runs.
# Fixed when, in both sized runs, the exporter enqueues every span, the receiver refuses nothing, and the gateway sends every
# span the sender generated, with a complete drain.
set -uo pipefail
cd "$(dirname "$0")"
. ./lib.sh
. ./measure.sh
. ./splunk.sh

start_clock 820 not_fixed

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
APP_NODE=$(cat app-node.txt); LOAD_NODE=$(cat load-node.txt)
[ -s results.txt ] || fail "reproduce.sh left no results"

echo "== The sized config's queue"
kubectl -n demo get configmap gateway -o jsonpath='{.data.sized\.yaml}' | sed -n '/^exporters/,/^service/p' | sed 's/^/  sized.yaml | /'

for r in sized-1 sized-2; do
  echo "== Run $r: sized.yaml, 50 s of load, the relay down from 10 s to 40 s"
  start_run "$r" 120
  outage_run "$r"
  measures "$r"
done

echo "== Summary, all six runs"
summary

echo "== Spans Splunk APM holds for the load service, per run window (read once, after a wait for APM)"
sleep "$(cap 90 240)"
for r in batch-1 migrated-1 batch-2 migrated-2 sized-1 sized-2; do
  apm_spans "$r" > "apm-$r.txt"; n=$(cat "apm-$r.txt")
  read -r _ s e < <(awk -v r="$r" '$1 == r' runs.txt)
  echo "$r: Splunk APM count $n for $(date -u -d @"$(( s / 10 * 10 ))" +%H:%M:%S)-$(date -u -d @"$(( (e + 29) / 10 * 10 ))" +%H:%M:%S) UTC, program: $(apm_program_for)"
  echo "$r: the sender generated $(col "$r" 2), the gateway sent $(col "$r" 6)"
  echo "HERO_MEASURE: splunk_apm_spans[$r]=$n"
  [ "$(remaining)" -gt 30 ] || { echo "note: out of time for the remaining Splunk reads"; break; }
done

ok=1
for r in sized-1 sized-2; do
  [ "$(col "$r" 5)" = 0 ] || ok=0
  [ "$(col "$r" 4)" = 0 ] || ok=0
  [ "$(col "$r" 7)" -le 0 ] || ok=0
  [ "$(col "$r" 12)" = 1 ] || ok=0
done
[ "$ok" = 1 ] && emit fixed || emit not_fixed
