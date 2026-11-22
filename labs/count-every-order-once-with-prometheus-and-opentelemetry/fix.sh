#!/usr/bin/env bash
# BUILD: two ways to scrape each pod once, nothing else changed. Two runs each:
#   node        the same scrape job, with a pod-discovery field selector for the agent's own node (values-node.yaml)
#   autodetect  no scrape job of our own: autodetect.prometheus=true, which scrapes annotated pods on the agent's node
# Then every run (the everywhere runs too) is read back from Splunk: the Prometheus counter, its series, and
# OpenTelemetry's own count of the same orders.
set -uo pipefail
cd "$(dirname "$0")"
. ./lib.sh
. ./splunk.sh

start_clock 830 not_fixed

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
for v in SPLUNK_REALM SPLUNK_ACCESS_TOKEN SPLUNK_API_TOKEN; do [ -n "${!v:-}" ] || fail "$v is not set"; done
NODE_A=$(cat node-a.txt) || fail "no node recorded"
[ -s truth-everywhere-1.txt ] && [ -s truth-everywhere-2.txt ] || fail "reproduce.sh's runs are missing"
CONFIG=everywhere

echo "== 1. The one change in values-node.yaml, and the one value in values-autodetect.yaml"
diff values-everywhere.yaml values-node.yaml | grep '^>' | grep -v '^> #' | sed 's/^>/  node:/'
grep -v '^#' values-autodetect.yaml | sed 's/^/  autodetect: /'

for run in node-1 node-2 autodetect-1 autodetect-2; do
  echo "== Run $run"
  start_run "$run" 150
  load_run "$run" "$(cap 90 230)"
  measures "$run"
done

echo "== 2. Every run in Splunk"
problems=0
for run in autodetect-2 everywhere-1 everywhere-2 node-1 node-2 autodetect-1; do   # the newest first, so its wait covers the rest
  if [ "$run" = autodetect-2 ]; then splunk_run_patiently "$run" "$(cap 200 120)"; else splunk_run_patiently "$run" "$(cap 60 100)"; fi
  want=$(cat "truth-$run.txt")
  [ -s "series-$run.json" ] && [ -s "metric-names-$run.txt" ] || { echo "  $run: no series or metric names were read from Splunk"; problems=$((problems + 1)); }
  echo "HERO_MEASURE: pod_orders[$run]=$want"
  echo "HERO_MEASURE: splunk_prometheus_orders[$run]=$prom_total"
  echo "HERO_MEASURE: splunk_prometheus_series[$run]=$prom_series"
  echo "HERO_MEASURE: splunk_otel_orders[$run]=$otel_total"
  [ "$per_pod" != error ] && echo "HERO_MEASURE: splunk_series_per_pod_max[$run]=$per_pod"
  [ "$dims_n" != error ] && echo "HERO_MEASURE: splunk_dims_splitting_a_pod[$run]=$dims_n"
  case "$run" in
    everywhere-*) ;;   # measured, not a condition: what Splunk shows when every agent scrapes every pod is the finding
    *) [ "$prom_total" = "$want" ] || { echo "  $run: Splunk's counter $prom_total, the pods counted $want"; problems=$((problems + 1)); }
       [ "$prom_series" = 2 ] && [ "$per_pod" = 1 ] || { echo "  $run: $prom_series series, $per_pod for one pod, expected one per pod"; problems=$((problems + 1)); } ;;
  esac
  [ "$otel_total" = "$want" ] || { echo "  $run: OpenTelemetry counted $otel_total, the pods $want"; problems=$((problems + 1)); }
done
# Who scraped each pod, from the pods' own records: every agent scraped every pod in the everywhere runs, and one agent
# each pod in the others. This ties Splunk's numbers to the cause.
for run in everywhere-1 everywhere-2 node-1 node-2 autodetect-1 autodetect-2; do
  pairs=0; expect=2; case "$run" in everywhere-*) expect=4 ;; esac
  while read -r pod orders rest; do for s in $rest; do [ "${s##*:}" -ge 2 ] && pairs=$(( pairs + 1 )); done; done < "pods-$run.txt"
  [ "$pairs" = "$expect" ] || { echo "  $run: $pairs scraper-pod pairs, expected $expect"; problems=$((problems + 1)); }
done
echo "problems: $problems"
[ "$problems" = 0 ] && emit fixed || emit not_fixed
