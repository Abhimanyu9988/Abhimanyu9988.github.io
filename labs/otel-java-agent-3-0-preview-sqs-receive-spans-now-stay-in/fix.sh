#!/usr/bin/env bash
# BUILD: the 3.0 preview. One environment variable, OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true, on both services: same jar, same image,
# same agent, same Collector, same queue, no code change. The same two runs as reproduce.sh, then sampled preview traces read back from Splunk.
set -uo pipefail
cd "$(dirname "$0")"
. ./lib.sh
. ./splunk.sh

start_clock 830 not_fixed

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
for v in SPLUNK_REALM SPLUNK_ACCESS_TOKEN SPLUNK_API_TOKEN; do [ -n "${!v:-}" ] || fail "$v is not set"; done
API="https://api.${SPLUNK_REALM}.signalfx.com"
LOAD_NODE=$(cat load-node.txt) || fail "no load node recorded"
queue_settings

echo "== 1. The before-check: the cluster's own data reaches Splunk"
wait_cluster "$(cap 150 600)"
echo "cluster metric time series in Splunk: ${cluster:-0}"
[ "${cluster:-0}" -gt 0 ] || fail "no cluster data reached Splunk, so a zero here would prove nothing"

echo "== 2. The whole change: OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true on both services, receive telemetry at its default"
attach preview 150
load_run preview 400 load-preview.txt "$(cap 170 420)"
sleep 8
collect preview
report preview
enough
[ "${produced:-0}" -gt 0 ] && [ "${consumed:-0}" -gt 0 ] || fail "the agent printed no producer or process spans"
# Exactly 10 producer trace IDs, spread evenly across the run, whatever the trace structure turned out to be.
cut -d' ' -f2 spans-producer.txt | sort -u > preview-producer-traces.txt
awk -v n="$(wc -l < preview-producer-traces.txt)" 'BEGIN {for (i = 0; i < 10; i++) pick[int(i * n / 10) + 1] = 1} NR in pick' preview-producer-traces.txt > sample-traces.txt
echo "-- one preview consumer trace from the agent's OTLP output, with parents and links"
span_tree

echo "== 3. One setting changed: receive telemetry on, on fulfilment only, preview still on, for 100 orders"
attach receive_preview 120
load_run receive_preview 100 load-receive-preview.txt "$(cap 90 300)"
sleep 8
collect receive_preview
report receive_preview
echo "-- one consumer trace, from the agent's OTLP output"
span_tree
enough
[ "${receive_spans:-0}" -gt 0 ] || fail "no receive or poll spans, so the agent did not apply the setting with the preview"

echo "== 4. What Splunk Observability Cloud shows: sampled preview traces, then the operation names a filter or chart in Splunk APM would use"
checked=$(grep -c . sample-traces.txt || true); : > found.txt; : > both.txt; : > ops-preview.txt; nfound=0; nboth=0
t0=$(date +%s)
limit=$(cap 360 60)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  for t in $(cat sample-traces.txt); do
    grep -q "^$t\$" both.txt && continue
    items=$(trace_items "$t")
    [ "$(jq 'length' <<<"$items" 2>/dev/null || echo 0)" -gt 0 ] || continue
    grep -q "^$t\$" found.txt || echo "$t" >> found.txt
    jq -r '.[] | "\(.serviceName) \(.operationName)"' <<<"$items" >> ops-preview.txt 2>/dev/null
    [ "$(jq -r '[.[].serviceName] | index("orders") != null and index("fulfilment") != null' <<<"$items" 2>/dev/null)" = true ] && echo "$t" >> both.txt
  done
  nfound=$(grep -c . found.txt || true); nboth=$(grep -c . both.txt || true)
  echo "  t+$(( $(date +%s) - t0 ))s: sampled preview traces Splunk returns=$nfound, holding both services=$nboth, of $checked"
  [ "$nboth" -ge "$checked" ] && break
  # Once every trace is found and two and a half minutes have passed, the structure is what it is.
  [ "$nfound" -ge "$checked" ] && [ $(( $(date +%s) - t0 )) -ge 150 ] && break
  sleep 20
done
: > ops-default.txt
for t in $(cat default-traces.txt 2>/dev/null); do trace_items "$t" | jq -r '.[] | "\(.serviceName) \(.operationName)"' >> ops-default.txt 2>/dev/null; done
for m in default preview; do
  sort -u "ops-$m.txt" -o "ops-$m.txt"
  echo "-- operation names Splunk APM holds for the $m traces (service, operation)"
  sed 's/^/  /' "ops-$m.txt"
  n=$(awk '{$1=""; print substr($0, 2)}' "ops-$m.txt" | grep -cE "^(send|process|receive|poll|delete) " || true)
  echo "$m operation names in Splunk that start with the operation: $n"
  echo "HERO_MEASURE: splunk_operation_first_names[$m]=$n"
done
echo "-- the preview service map in Splunk (environment $LAB_NAME-v3)"
show_map <<<"$(topology "$LAB_NAME-v3")"
echo "sampled preview traces Splunk returns: $nfound of $checked"
echo "sampled preview traces holding both services in Splunk: $nboth of $checked"
echo "HERO_MEASURE: splunk_preview_traces_found=$nfound"
echo "HERO_MEASURE: splunk_preview_traces_both_services=$nboth"

# Whether the preview keeps one trace is the result, not the gate. The gate is that every run completed and Splunk returned the sample.
if [ "$checked" -eq 10 ] && [ "$nfound" -eq 10 ] && [ -s ops-default.txt ]; then
  emit fixed
else
  emit not_fixed
fi
