#!/usr/bin/env bash
# BUILD: the opt-in went on at the end of reproduce.sh. If Splunk didn't store its series, the attribute trim goes on here.
# Same jar, image, agent options and Collector, and no code change.
# Then read the new metric from Splunk for four statements. One has pg_sleep(0.03) in it, so its reading has a known
# answer, and pg_stat_statements gives a server-side reference. lib.sh and tools.sh hold read_statement, read_known, other_routes, compare_counts and same_as_before.
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
[ -n "${SPLUNK_REALM:-}" ] || fail "SPLUNK_REALM is not set"
[ -n "${SPLUNK_API_TOKEN:-}" ] || fail "SPLUNK_API_TOKEN is not set"
source ./lib.sh
LOAD_NODE=$(cat load-node.txt) || fail "no load node recorded"
newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }

echo "== 2. What the opt-in alone showed"
source ./probe.env
if [ "$title_changes" -eq 2 ]; then
  echo "the opt-in alone wasn't stored, so the second change goes on: the attribute trim"
  i=1
  for s in orders inventory; do
    kubectl -n demo set env "deployment/$s" "JAVA_TOOL_OPTIONS=$(sed -n ${i}p agent-options.txt) $TRIM" >/dev/null || fail "could not trim $s"
    i=$(( i + 1 ))
  done
  kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 400)s" >/dev/null || fail "inventory did not roll out"
  kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 400)s" >/dev/null || fail "orders did not roll out"
else
  echo "the opt-in alone was stored, so one setting is the whole change and these pods carry on"
fi

same_as_before
exporter_config

echo "== 3. The load: it aims for one request every 50 ms for 540 seconds, the first 30 seconds as warm-up"
pg "select pg_stat_statements_reset()" >/dev/null
read -r steal0 total0 <<<"$(cpu_stat "$(newest inventory)")"
kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
sed "s/LOAD_NODE/$LOAD_NODE/" k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 580 150)s" || { kubectl -n demo logs load --tail=20; fail "the load did not finish"; }
kubectl -n demo logs load | tee load-after.txt
grep -q 'errors=0' load-after.txt || fail "the load saw errors, so this run can't be used"
t_start=$(grep -oE 'started_epoch=[0-9]+' load-after.txt | cut -d= -f2)
[ -n "$t_start" ] || fail "the load printed no start time"
read -r steal1 total1 <<<"$(cpu_stat "$(newest inventory)")"
steal=$(awk -v s=$(( ${steal1:-0} - ${steal0:-0} )) -v t=$(( ${total1:-0} - ${total0:-0} )) 'BEGIN{printf "%.2f", (t > 0 ? s / t * 100 : 0)}')
credits=$(cpu_credits "$(cat app-node.txt)" "$(( t_start - 600 ))" "$(date +%s)")
echo "Read the latency numbers as context only if credits reach zero or steal passes 5%."

# One reading per minute, anchored to the time the load itself printed. A point holds exports from its minute, and each
# export covers the 60 seconds before it, so starting at 150 seconds keeps every export inside the load, after warm-up.
sent=$(grep -oE 'requests=[0-9]+' load-after.txt | cut -d= -f2)
achieved=$(awk -v r="${sent:-0}" 'BEGIN{printf "%.1f", r / 510}')
echo "achieved rate: $sent requests over the 510 seconds after warm-up, $achieved per second"
from_ms=$(( (t_start + 150) * 1000 ))
to_ms=$(( (t_start + 540) * 1000 ))
window=$(( to_ms / 60000 - (from_ms + 59999) / 60000 + 1 ))

echo "== 4. Reading the metric back from Splunk Observability Cloud"
n=0; known=0
t0=$(date +%s)
limit=$(cap 150 110)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  duration_series > series.json
  if [ "$(jq '.results | length' series.json 2>/dev/null || echo 0)" -gt 0 ]; then
    read_statement "select stock" lookup
    read_statement "select reserved" known
    n=$(grep -c . lookup-minutes.jsonl || true)
    known=$(grep -c . known-minutes.jsonl || true)
  fi
  echo "  t+$(( $(date +%s) - t_start ))s from the load's start: series=$(jq '.results | length' series.json 2>/dev/null) of $window minutes: lookup=$n known=$known"
  [ "$n" -ge "$window" ] && [ "$known" -ge "$window" ] && break
  sleep 20
done
echo "-- dimensions Splunk holds on ${METRIC}_bucket"
dimensions
echo "statement filter used: $(statement_filter "select stock")"
report_families
[ "$known" -ge 1 ] && [ "$n" -ge 1 ] || { echo "SignalFlow reply: $(head -c 400 signalflow.txt | tr '\n' ' ')"; fail "no readings came back from the SignalFlow stream"; }
echo "stream resolution: $(grep -oE '"resolutionMs" *: *[0-9]+' signalflow.txt | head -1)"

echo "-- $METRIC in Splunk, from /v2/metrictimeseries"
breakdown series.json
echo "-- database metric names in Splunk with the setting"
raw_names "sf_metric:db.* AND deployment.environment:${LAB_NAME}" | tee names-after.txt | sed 's/^/  /'

read_known

echo "beside that p95: CPU steal on the app node during the load ${steal}%, ${credits:-CPU credit balance not available here}"
timer_bucket "$client_p95" "$p95"
echo "the timer's p95 of ${client_p95:-none} ms sits in the stored bucket from $timer_lower to $timer_upper ms, and the SignalFlow p95 of ${p95:-none} ms is in that bucket: $in_bucket"

echo "-- the lookup, one reading per minute: $(stats lookup-minutes.jsonl)"
compare_counts
queries_gap=$(gap "$queries" "$expected")
echo "lookups Splunk counted: $queries, requests the load sent for those minutes: $expected ($low to $high by export phase), minutes compared: $compared"
echo "minutes dropped because the load's own sent count dipped: $(( window - compared )), minutes with no Splunk point: $empty"
echo "queries gap: ${queries_gap}%"

echo "== 5. The other two routes, on one short load with console output added"
other_routes

for pair in "lookup:select stock" "known:select reserved" "update:update stock" "audit:select audit"; do
  v=$(series_for series.json "${pair#*:}")
  echo "series for ${pair%%:*} (${pair#*:}): ${v:-0}"
  echo "HERO_MEASURE: series_per_statement[${pair%%:*}]=${v:-0}"
done
trim_numbers
echo "known query p95 ms ${p95:-none}, known query p95 vs truth pct $truth_gap, known query mean gap pct $mean_gap, queries gap pct $queries_gap"
echo "achieved rps $achieved, db duration max dimensions ${max_dimensions:-0}"
for kv in "dup_emits_both_names=$dup_sets" "pool_metric_names_after=$new_pool" "where_the_setting_is_read=$routes"; do
  echo "setting route results: ${kv%%=*} ${kv#*=}"
  echo "HERO_MEASURE: setting_route_results[${kv%%=*}]=${kv#*=}"
done
for kv in "untrimmed_resource_attribute_count=$untrimmed" "trimmed_resource_attribute_count=$trimmed" "collector_added_attribute_count=$collector_added" "untrimmed_db_series_dimensions=$untrimmed_dims"; do
  echo "trim evidence: ${kv%%=*} ${kv#*=}"
  echo "HERO_MEASURE: trim_evidence[${kv%%=*}]=${kv#*=}"
done
echo "known query p95 in timer bucket $in_bucket, known query p95 from program $from_program"
echo "known query p95 minutes $known, known query bucket lower ms $lower, known query bucket upper ms $upper"
if [ "$from_program" -eq 1 ]; then
  echo "HERO_MEASURE: known_query_p95_ms=$p95"
  echo "HERO_MEASURE: known_query_p95_vs_truth_pct=$truth_gap"
fi
echo "HERO_MEASURE: known_query_p95_bucket_crosscheck_ms=$crosscheck"
echo "HERO_MEASURE: known_query_p95_in_timer_bucket=$in_bucket"
echo "HERO_MEASURE: known_query_p95_from_program=$from_program"
echo "HERO_MEASURE: known_query_mean_gap_pct=$mean_gap"
echo "HERO_MEASURE: queries_gap_pct=$queries_gap"
echo "HERO_MEASURE: achieved_rps=$achieved"
echo "HERO_MEASURE: db_duration_max_dimensions=${max_dimensions:-0}"
echo "HERO_MEASURE: known_query_p95_minutes=$known"
echo "HERO_MEASURE: known_query_bucket_lower_ms=$lower"
echo "HERO_MEASURE: known_query_bucket_upper_ms=$upper"

if [ "$n" -ge 6 ] && [ "$known" -ge 6 ] && [ "$dup_sets" -eq 2 ] && [ "$new_pool" -ge 1 ] && [ "$routes" -eq 2 ] && [ -n "$client_p95" ] && [ "$in_bucket" -eq 1 ] && [ "$from_program" -eq 1 ] && [ "${max_dimensions:-99}" -le 36 ] && [ "$agrees" -eq 1 ] && awk -v a="$mean_gap" -v b="$queries_gap" 'BEGIN{exit !(a <= 10 && b <= 10)}'; then
  emit fixed
else
  emit not_fixed
fi
