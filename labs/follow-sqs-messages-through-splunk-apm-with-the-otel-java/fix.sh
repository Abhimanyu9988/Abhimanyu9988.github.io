#!/usr/bin/env bash
# BUILD: one environment variable on each service. Same jar, same image, same Collector, same queue, no code change.
# Then join each message's producer and process spans on its SQS message ID, and read a sample of traces back from Splunk.
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
AGENT_VERSION=2.33.1    # pinned in k8s/00-build.yaml with its SHA-256. The defaults this lab measures are this version's.

# The whole change. console and logging-otlp print spans to the pod log as well as sending them, so the lab can count every message and read links.
options() {   # service, exporters, then any extra setting
  echo "-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$1 -Dotel.exporter.otlp.endpoint=http://\$(HOST_IP):4318 -Dotel.resource.attributes=deployment.environment=$LAB_NAME,deployment.environment.name=$LAB_NAME -Dotel.logs.exporter=none -Dotel.traces.exporter=$2${3:+ $3}"
}

echo "== 2. The before-check: the cluster's own data reaches Splunk while the services send nothing"
wait_cluster "$(cap 150 500)"
echo "cluster metric time series in Splunk: ${cluster:-0}"
[ "${cluster:-0}" -gt 0 ] || fail "no cluster data reached Splunk, so a zero here would prove nothing"

echo "== 3. The whole change: one environment variable on each service, and no SQS setting among its values"
kubectl -n demo set env deployment/orders "JAVA_TOOL_OPTIONS=$(options orders otlp,console)" >/dev/null || fail "could not set the variable on orders"
kubectl -n demo set env deployment/fulfilment "JAVA_TOOL_OPTIONS=$(options fulfilment otlp,console,logging-otlp)" >/dev/null || fail "could not set the variable on fulfilment"
kubectl -n demo rollout status deploy/fulfilment --timeout="$(cap 120 450)s" >/dev/null || { why fulfilment; fail "fulfilment did not roll out"; }
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 450)s" >/dev/null || { why orders; fail "orders did not roll out"; }
for s in orders fulfilment; do
  p=$(newest "$s")
  echo "$s image: $(kubectl -n demo get "$p" -o jsonpath='{.spec.containers[0].image}')"
  echo "$s jar: $(kubectl -n demo exec "$p" -c app -- sha256sum /lab/app.jar)"
  echo "$s agent jar: $(kubectl -n demo exec "$p" -c app -- sha256sum /lab/opentelemetry-javaagent.jar)"
  version=$(kubectl -n demo logs "$p" -c app 2>/dev/null | grep -oE 'opentelemetry-javaagent - version: [0-9.]+' | awk 'NR == 1 {print $NF}')
  echo "$s agent version: ${version:-unknown}"
  [ "$version" = "$AGENT_VERSION" ] || fail "$s runs agent ${version:-unknown}, not $AGENT_VERSION"
  effective "$s"
done
[ "$(kubectl -n demo exec "$(newest orders)" -c app -- sha256sum /lab/app.jar | awk '{print $1}')" = "$(cat jar-sha.txt)" ] && echo "jar unchanged from the before run" || fail "the jar changed, so the comparison would be unfair"

echo "== 4. The same 400 orders, with the agent on its defaults"
load_run 400 load-after.txt "$(cap 170 400)"
sleep 8
collect after
echo "default run: sent $sent, fulfilled $handled, producer spans $produced, consumer spans $consumed, joined on message ID $joined, sharing one trace $shared, distinct traces $traces"
echo "default run, misses: no process span $no_consumer, no producer span $no_producer, different trace $different. Most process spans for one message: ${most:-0}"
one_trace_pct=$(awk -v s="$shared" -v n="$sent" 'BEGIN{printf "%.1f", s * 100 / n}')
echo "one trace percent, default run: $one_trace_pct"
echo "default run, consumer spans linked to the producer: $linked"
echo "default_receive_span links to the producer: $linked_receive"
echo "default run, receive spans: $receive_spans"
echo "-- three of the joined rows: message ID, producer trace, consumer trace"
awk 'NR <= 3 {print "  " $0}' spans-joined.txt
echo "HERO_MEASURE: messages_sent[default]=$sent"
echo "HERO_MEASURE: one_trace_messages[default]=$shared"
echo "HERO_MEASURE: one_trace_percent=$one_trace_pct"
echo "HERO_MEASURE: consumer_spans_linked_to_producer[default]=$linked"
echo "HERO_MEASURE: consumer_spans_linked_to_producer[default_receive_span]=$linked_receive"
echo "HERO_MEASURE: receive_spans[default]=$receive_spans"
# Exactly 10 trace IDs, spread evenly across the run. With fewer to pick from, the gate below can't pass.
awk '$2 == $3 {print $2}' spans-joined.txt | sort -u > shared-traces.txt
awk -v n="$(wc -l < shared-traces.txt)" 'BEGIN {for (i = 0; i < 10; i++) pick[int(i * n / 10) + 1] = 1} NR in pick' shared-traces.txt > sample-traces.txt

echo "== 5. How the trace crossed the queue: fulfilment pauses, one order goes in, and that message is read from the queue"
probe
with_header=$(grep -c . <<<"$header" || true)
echo "default run, messages carrying the header: $with_header"
echo "HERO_MEASURE: trace_header_on_message[default]=$with_header"
sleep 3
kubectl -n demo logs "$(newest orders)" -c app > orders-probe.log 2>/dev/null
span_trace=$(span_rows PRODUCER orders-probe.log | awk -v m="$probe_id" '$1 == m {print $2}')
root=$(trace_root <<<"$header")
echo "trace ID in the header: ${root:-none}, on the producer span: ${span_trace:-none}"
matches=$([ -n "$root" ] && [ "$root" = "$span_trace" ] && echo 1 || echo 0)
echo "trace header root matches the producer span: $matches"
echo "HERO_MEASURE: trace_header_root_matches=$matches"
resume "$(cap 90 300)"

echo "== 6. One setting changed: receive telemetry on, on fulfilment only, for 100 orders"
SETTING=-Dotel.instrumentation.common.messaging.experimental.receive-telemetry.enabled=true
kubectl -n demo set env deployment/fulfilment "JAVA_TOOL_OPTIONS=$(options fulfilment otlp,console,logging-otlp "$SETTING")" >/dev/null || fail "could not set the option"
kubectl -n demo rollout status deploy/fulfilment --timeout="$(cap 90 280)s" >/dev/null || { why fulfilment; fail "fulfilment did not roll out"; }
effective fulfilment
load_run 100 load-receive.txt "$(cap 60 240)"
sleep 8
collect receive
echo "receive_telemetry run: sent $sent, fulfilled $handled, joined on message ID $joined, sharing one trace $shared, in their own trace $different. Most process spans for one message: ${most:-0}"
echo "receive_telemetry run, consumer spans linked to the producer: $linked"
echo "receive_telemetry_receive_span links to the producer: $linked_receive"
echo "receive_telemetry run, process spans whose parent is a receive span: $under_receive"
echo "-- one consumer trace, from the agent's OTLP output"
span_tree
echo "receive_telemetry run, receive spans: $receive_spans"
echo "HERO_MEASURE: messages_sent[receive_telemetry]=$sent"
echo "HERO_MEASURE: one_trace_messages[receive_telemetry]=$shared"
echo "HERO_MEASURE: consumer_spans_linked_to_producer[receive_telemetry]=$linked"
echo "HERO_MEASURE: consumer_spans_linked_to_producer[receive_telemetry_receive_span]=$linked_receive"
echo "HERO_MEASURE: receive_spans[receive_telemetry]=$receive_spans"
[ "${receive_spans:-0}" -gt 0 ] || fail "no receive spans, so the agent did not apply the setting"

echo "== 7. What Splunk Observability Cloud shows for the default run"
map=""; services=0; both=0; checked=$(grep -c . sample-traces.txt || true); : > confirmed.txt
t0=$(date +%s)
limit=$(cap 420 25)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  map=$(topology)
  services=$(real_services <<<"$map")
  for t in $(cat sample-traces.txt); do
    grep -q "^$t\$" confirmed.txt && continue
    items=$(trace_items "$t")
    [ "$(jq -r '[.[].serviceName] | index("orders") != null and index("fulfilment") != null' <<<"$items" 2>/dev/null)" = true ] && { echo "$t" >> confirmed.txt; last=$items; }
  done
  both=$(grep -c . confirmed.txt || true)
  echo "  t+$(( $(date +%s) - t0 ))s: services=${services:-0} sampled traces holding both services=$both of $checked"
  [ "${services:-0}" -ge 2 ] && [ "$both" -ge "$checked" ] && break
  sleep 20
done
show_map <<<"$map"
path=$(queue_path <<<"$map")
echo "services on the map from Splunk: ${services:-0}"
echo "queue path on map from orders to fulfilment: ${path:-0}"
echo "HERO_MEASURE: services_on_map=${services:-null}"
echo "HERO_MEASURE: queue_path_on_map=${path:-0}"
[ "$both" -ge 1 ] && show_trace "$(awk 'NR == 1' confirmed.txt)" "${last:-[]}"
echo "sampled traces with both services in one trace in Splunk: $both of $checked"
echo "HERO_MEASURE: splunk_traces_both_services=$both"
echo "HERO_MEASURE: splunk_traces_checked=$checked"

# The percent is a result, not a gate. It only picks the title, under the rule in the brief.
if [ "$checked" -eq 10 ] && [ "$both" -eq 10 ] && [ "${services:-0}" -eq 2 ]; then
  emit fixed
else
  emit not_fixed
fi
