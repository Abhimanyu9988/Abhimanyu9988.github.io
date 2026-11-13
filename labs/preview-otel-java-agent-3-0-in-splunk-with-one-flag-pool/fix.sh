#!/usr/bin/env bash
# AFTER: a preview pair (orders-v3, inventory-v3) starts next to the default pair on the same node: same jar, image,
# Collector and agent options, plus OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true and its own deployment.environment.
# Three rounds of the same load hit both pairs at once, so each round gives one reading per mode over the same minutes.
set -uo pipefail
cd "$(dirname "$0")"

START=$(date +%s)
LIMIT=830
HERO_DONE=0
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; type diagnose >/dev/null 2>&1 && diagnose; [ "$HERO_DONE" = 1 ] || emit not_fixed; exit 0; }
trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: not_fixed"; }' EXIT

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
[ -n "${SPLUNK_REALM:-}" ] && [ -n "${SPLUNK_API_TOKEN:-}" ] || fail "the Splunk settings are not set"
source ./lib.sh
LOAD_NODE=$(cat load-node.txt) || fail "no load node recorded"
newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }

echo "== 1. Starting the preview pair next to the default pair: one environment variable added"
for s in inventory orders; do
  kubectl -n demo get deploy "$s" -o json | jq --arg s "$s" --arg d "$DEFAULT_ENV" --arg p "$PREVIEW_ENV" '
    {apiVersion, kind, metadata: {name: "\($s)-v3", namespace: "demo"},
     spec: (.spec | .selector.matchLabels.app = "\($s)-v3" | .template.metadata.labels.app = "\($s)-v3"
       | .template.spec.containers[0].env |= (map(
           if .name == "JAVA_TOOL_OPTIONS" then .value |= sub("deployment.environment=" + $d; "deployment.environment=" + $p)
           elif .name == "INVENTORY_URL" then .value = "http://inventory-v3:8080" else . end)
         + [{name: "OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW", value: "true"}]))}' | kubectl apply -f - >/dev/null || fail "could not create $s-v3"
  kubectl -n demo expose deploy "$s-v3" --port 8080 --target-port 8080 >/dev/null 2>&1 || true
done
kubectl -n demo rollout status deploy/inventory-v3 --timeout="$(cap 150 600)s" >/dev/null || fail "inventory-v3 did not start"
kubectl -n demo rollout status deploy/orders-v3 --timeout="$(cap 150 600)s" >/dev/null || fail "orders-v3 did not start"
for s in orders inventory orders-v3 inventory-v3; do
  p=$(newest "$s")
  echo "$s: node $(kubectl -n demo get "$p" -o jsonpath='{.spec.nodeName}'), jar $(kubectl -n demo exec "$p" -c app -- sha256sum /lab/app.jar | awk '{print $1}')"
  kubectl -n demo get "$p" -o json | jq -r '.spec.containers[0].env[] | select(.name == "JAVA_TOOL_OPTIONS" or (.name | startswith("OTEL_"))) | "  \(.name)=\(.value)"'
  [ "$(kubectl -n demo exec "$p" -c app -- sha256sum /lab/app.jar | awk '{print $1}')" = "$(cat jar-sha.txt)" ] || fail "$s runs a different jar"
done
echo "same jar in all four pods: yes"

echo "== 2. Three rounds: the same load on both pairs at the same time, 90 seconds each, the first 30 as warm-up"
warm_preview
: > rounds.txt
for r in 1 2 3; do
  kubectl -n demo delete pod "load-d$r" "load-v$r" --ignore-not-found --wait=true >/dev/null 2>&1
  load_pod "load-d$r" orders || fail "could not start round $r for the default pair"
  load_pod "load-v$r" orders-v3 || fail "could not start round $r for the preview pair"
  for p in "load-d$r" "load-v$r"; do
    kubectl -n demo wait "pod/$p" --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 130 200)s" >/dev/null || { kubectl -n demo logs "$p" --tail=10; fail "round $r: $p did not finish"; }
  done
  end=$(date +%s)
  for p in "load-d$r" "load-v$r"; do kubectl -n demo logs "$p" > "$p.txt"; grep -E 'target=|requests=' "$p.txt" | sed "s/^/  round $r $p: /"; done
  start=$(grep -oE 'started_epoch=[0-9]+' "load-d$r.txt" | cut -d= -f2)
  echo "$r $start $end" >> rounds.txt
  [ "$r" -lt 3 ] && sleep 60   # a quiet minute, so each round's window holds only that round
done

load_counts
source ./load.env
rate_gap=$(awk -v a="$default_rps" -v b="$preview_rps" 'BEGIN{d = (a - b) / b * 100; printf "%.1f", (d < 0 ? -d : d)}')
echo "the two pairs' rates differ by ${rate_gap}%"
[ "$default_errors" -eq 0 ] && [ "$preview_errors" -eq 0 ] || fail "requests failed during the rounds"
# The same load means rates within 5% of each other and near the 20 per second target. The Postgres lab reached 19.1
# per second for one pair on this node, so the floor is 18.
awk -v g="$rate_gap" -v a="$default_rps" -v b="$preview_rps" 'BEGIN{ exit !(g <= 5 && a >= 18 && a <= 21 && b >= 18 && b <= 21) }' \
  || fail "the pairs did not get the same load: $default_rps and $preview_rps per second, ${rate_gap}% apart"

echo "== 3. What Splunk Observability Cloud stores for each pair"
pool=0
t0=$(date +%s)
limit=$(cap 150 300)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  pool=$(pool_series "$PREVIEW_ENV" "$NEW_POOL" inventory)
  echo "  t+$(( $(date +%s) - t0 ))s: preview pool series under $NEW_POOL.* for inventory: ${pool:-0}"
  [ "${pool:-0}" -gt 0 ] && break
  sleep 20
done
[ "${pool:-0}" -gt 0 ] || fail "no pool metric under the new names reached Splunk"
sleep 75   # the last round's minute closes up to 60 s after it ends, then Splunk needs time
echo "-- database metric names in Splunk with the agent's defaults"
names_in "$DEFAULT_ENV" | tee names-default.txt | sed 's/^/  /'
echo "-- database metric names in Splunk with the 3.0 preview"
names_in "$PREVIEW_ENV" | tee names-preview.txt | sed 's/^/  /'
echo "-- names in only one mode"
comm -3 names-default.txt names-preview.txt | sed 's/^\t/  preview only: /; t; s/^/  default only: /'
new_min=999; legacy_total=0
for s in orders inventory; do
  n=$(pool_series "$PREVIEW_ENV" "$NEW_POOL" "$s"); l=$(pool_series "$PREVIEW_ENV" "$LEGACY_POOL" "$s")
  echo "preview, $s: pool series under $NEW_POOL.* ${n:-0}, under $LEGACY_POOL.* ${l:-0}"
  echo "HERO_MEASURE: preview_new_pool_series[$s]=${n:-0}"
  echo "HERO_MEASURE: preview_legacy_pool_series[$s]=${l:-0}"
  [ "${n:-0}" -lt "$new_min" ] && new_min=${n:-0}
  legacy_total=$(( legacy_total + ${l:-0} ))
done
ops=$(op_series "$PREVIEW_ENV")
echo "preview: db.client.operation.duration series $ops"
echo "HERO_MEASURE: operation_duration_series[preview]=${ops:-0}"

echo "-- the same pool timing per round, read for both pairs over the same minutes"
ratios_ok=1; ratio_max=0
while read -r r s e; do
  from_ms=$(( s * 1000 )); to_ms=$(( (e + 59) * 1000 ))
  dm=$(use_time_mean "$DEFAULT_ENV" "$LEGACY_POOL" "$from_ms" "$to_ms")
  pm=$(use_time_mean "$PREVIEW_ENV" "$NEW_POOL" "$from_ms" "$to_ms")
  [ -n "$dm" ] && [ -n "$pm" ] || { echo "round $r: a mean could not be read (default '${dm}', preview '${pm}')"; ratios_ok=0; continue; }
  ratio=$(awk -v a="$dm" -v b="$pm" 'BEGIN{ if (b > 0) printf "%.1f", a / b }')
  echo "round $r: default $dm (stored in ms), preview $pm (stored in s), ratio ${ratio:-none}"
  echo "stored use time, round $r: default $dm"
  echo "stored use time, round $r: preview $pm"
  [ -n "$ratio" ] && echo "HERO_MEASURE: use_time_ratio[round_$r]=$ratio"
  ratio_max=$(awk -v x="${ratio:-0}" -v m="$ratio_max" 'BEGIN{ print (x > m ? x : m) }')
  awk -v x="${ratio:-0}" 'BEGIN{ exit !(x >= 500 && x <= 2000) }' || ratios_ok=0
done < rounds.txt
echo "HERO_MEASURE: use_time_ratio_max=$ratio_max"
echo "every ratio within 500 to 2000: $([ "$ratios_ok" = 1 ] && echo yes || echo no)"

echo "-- database spans on one trace per pair, from Splunk APM"
dt=$(grep -oE 'last_trace_id=[0-9a-f]+' load-d3.txt | cut -d= -f2)
pt=$(grep -oE 'last_trace_id=[0-9a-f]+' load-v3.txt | cut -d= -f2)
fetch_trace "$dt" trace-default-round3.json || echo "  trace $dt: no database spans yet"
fetch_trace "$pt" trace-preview-round3.json || echo "  trace $pt: no database spans yet"
echo "default pair, trace $dt:"; db_spans trace-default-round3.json | sed 's/^/  /'
echo "preview pair, trace $pt:"; db_spans trace-preview-round3.json | sed 's/^/  /'
dk=$(db_keys trace-default-round3.json); pk=$(db_keys trace-preview-round3.json)
echo "database span keys, default: ${dk:-none found}"
echo "database span keys, preview: ${pk:-none found}"
p_old=$(count_in "$pk" "$LEGACY_KEYS"); p_new=$(count_in "$pk" "$NEW_KEYS"); p_svc=$(db_services trace-preview-round3.json)
echo "preview keys: old $p_old, new $p_new, from $p_svc services"
echo "HERO_MEASURE: span_db_keys[preview_old]=$p_old"
echo "HERO_MEASURE: span_db_keys[preview_new]=$p_new"
echo "HERO_MEASURE: preview_old_span_keys=$p_old"

[ "$new_min" -ge 1 ] && [ "$legacy_total" -eq 0 ] && [ "$p_new" -ge 1 ] && [ "$p_old" -eq 0 ] && [ "$p_svc" -ge 2 ] && [ "$ratios_ok" = 1 ] \
  || { echo "the preview pair did not show only new names from both services, or a ratio left 500 to 2000"; emit not_fixed; exit 0; }
emit fixed
