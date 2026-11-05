#!/usr/bin/env bash
# BUILD: one environment variable on each service. Same jar, same image, same Collector, no code change.
# Then count what Splunk Observability Cloud shows, and measure the cost the same way as the before run.
set -uo pipefail
cd "$(dirname "$0")"

START=$(date +%s)
LIMIT=830
HERO_DONE=0
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit not_fixed; exit 0; }
trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: not_fixed"; }' EXIT

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
[ -n "${SPLUNK_REALM:-}" ] || fail "SPLUNK_REALM is not set"
[ -n "${SPLUNK_ACCESS_TOKEN:-}" ] || fail "SPLUNK_ACCESS_TOKEN is not set"
[ -n "${SPLUNK_API_TOKEN:-}" ] || fail "SPLUNK_API_TOKEN is not set"
API="https://api.${SPLUNK_REALM}.signalfx.com"
LOAD_NODE=$(cat load-node.txt) || fail "no load node recorded"

newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }

startup() {
  local pod line="" i
  pod=$(newest "$1")
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    line=$(kubectl -n demo logs "$pod" -c app 2>/dev/null | grep -oE 'process running for [0-9.]+' | tail -1)
    [ -n "$line" ] && break
    sleep 3
  done
  echo "${line##* }"
}

series() { curl -sS --max-time 20 -G -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/metrictimeseries" --data-urlencode "query=$1" --data-urlencode "limit=1000" 2>/dev/null; }

# Agent metrics by family: Splunk stores a histogram as name_bucket, name_count and so on, which is one metric to a reader.
agent_names() {
  { series "deployment.environment:${LAB_NAME}"; series "sf_metric:jvm* AND k8s.cluster.name:${LAB_NAME}"; series "sf_metric:http* AND k8s.cluster.name:${LAB_NAME}"; } |
    jq -rs '[.[].results[]?.metric // empty | select(test("^(jvm|http|db|hikaricp|tomcat|executor)\\.")) | sub("_(bucket|count|sum|min|max)$"; "")] | unique | .[]' 2>/dev/null
}

topology() {   # Splunk's Go client sends start/end in epoch milliseconds. ISO times are tried second.
  local now range body out
  now=$(jq -rn 'now | floor')
  for range in "$(( (now - 1500) * 1000 ))/$(( now * 1000 ))" "$(jq -rn --argjson n "$now" '$n - 1500 | todate')/$(jq -rn --argjson n "$now" '$n | todate')"; do
    body=$(jq -cn --arg t "$range" --arg e "$LAB_NAME" '{timeRange: $t, tagFilters: [{name: "sf_environment", operator: "equals", scope: "GLOBAL", value: $e}]}')
    out=$(curl -sS --max-time 20 -X POST -H "X-SF-Token: $SPLUNK_API_TOKEN" -H "Content-Type: application/json" -d "$body" "$API/v2/apm/topology" 2>/dev/null)
    jq -e '.data' <<<"$out" >/dev/null 2>&1 && break
  done
  echo "$out"
}

trace_spans() {   # the newest segment, or every segment when that route has nothing
  local out seg
  out=$(curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/latest" 2>/dev/null)
  if jq -e 'type == "array" and length > 0' <<<"$out" >/dev/null 2>&1; then echo "$out"; return; fi
  for seg in $(curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/segments" 2>/dev/null | jq -r '.[]?' 2>/dev/null); do
    curl -sS --max-time 20 -H "X-SF-Token: $SPLUNK_API_TOKEN" "$API/v2/apm/trace/$1/$seg" 2>/dev/null
  done | jq -s 'map(if type == "array" then . else [] end) | add // []' 2>/dev/null
}

echo "== 2. The before-check: the cluster's own data reaches Splunk while the services send nothing"
cluster=0
t0=$(date +%s)
limit=$(cap 150 500)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  cluster=$(series "k8s.cluster.name:${LAB_NAME}" | jq -r '.count // (.results | length) // 0' 2>/dev/null || echo 0)
  [ "${cluster:-0}" -gt 0 ] && break
  sleep 15
done
echo "cluster metric time series in Splunk: ${cluster:-0}"
before=$(agent_names | grep -c . || true)
echo "agent metric names in Splunk before the change: ${before:-0}"
[ "${cluster:-0}" -gt 0 ] || fail "no cluster data reached Splunk, so a zero here would prove nothing"

echo "== 3. The whole change: one environment variable on each service"
for s in orders inventory; do
  kubectl -n demo set env "deployment/$s" "JAVA_TOOL_OPTIONS=-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$s -Dotel.exporter.otlp.endpoint=http://\$(HOST_IP):4318 -Dotel.resource.attributes=deployment.environment=$LAB_NAME,deployment.environment.name=$LAB_NAME -Dotel.logs.exporter=none" >/dev/null || fail "could not set the variable on $s"
done
why() { kubectl -n demo get pods -o wide | tail -8 | cut -c1-120; kubectl -n demo get events --sort-by=.lastTimestamp | tail -10 | cut -c1-180; }
kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 400)s" >/dev/null || { why; fail "inventory did not roll out"; }
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 400)s" >/dev/null || { why; fail "orders did not roll out"; }

for s in orders inventory; do
  p=$(newest "$s")
  echo "$s image: $(kubectl -n demo get "$p" -o jsonpath='{.spec.containers[0].image}')"
  echo "$s jar: $(kubectl -n demo exec "$p" -c app -- sha256sum /lab/app.jar)"
  kubectl -n demo logs "$p" -c app 2>/dev/null | grep -m1 'opentelemetry-javaagent - version' | sed "s/^/$s /"
done
same=$(kubectl -n demo exec "$(newest orders)" -c app -- sha256sum /lab/app.jar | awk '{print $1}')
[ "$same" = "$(cat jar-sha.txt)" ] && echo "jar unchanged from the before run" || fail "the jar changed, so the comparison would be unfair"

echo "== 4. Startup, three samples of each service"
: > startup-after.txt
for round in 1 2 3; do
  if [ "$round" != 1 ]; then
    kubectl -n demo rollout restart deploy/inventory deploy/orders >/dev/null || fail "restart failed"
    kubectl -n demo rollout status deploy/inventory --timeout="$(cap 90 330)s" >/dev/null || fail "inventory did not restart"
    kubectl -n demo rollout status deploy/orders --timeout="$(cap 90 330)s" >/dev/null || fail "orders did not restart"
  fi
  for s in orders inventory; do
    v=$(startup "$s")
    [ -n "$v" ] || fail "no startup line for $s"
    echo "$s startup round $round: process running for $v"
    echo "$s $v" >> startup-after.txt
  done
done

echo "== 5. The same load as the before run"
t_traffic=$(date +%s)
kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
sed "s/LOAD_NODE/$LOAD_NODE/" k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 170 200)s" || { kubectl -n demo logs load --tail=20; fail "the load did not finish"; }
kubectl -n demo logs load | tee load-after.txt
TRACE=$(grep -oE 'last_trace_id=[0-9a-f]{32}' load-after.txt | cut -d= -f2)
[ -n "$TRACE" ] || fail "no trace ID from the load"

: > memory-after.txt
for s in orders inventory; do
  v=$(kubectl -n demo exec "$(newest "$s")" -c app -- grep VmRSS /proc/1/status | awk '{print $2}')
  [ -n "$v" ] || fail "no memory value for $s"
  echo "$s VmRSS: $v kB"
  echo "$s $v" >> memory-after.txt
done

echo "== 6. What Splunk Observability Cloud now shows"
map=""; services=0; names=0; spans=0; raw=""
t0=$(date +%s)
limit=$(cap 480 70)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  map=$(topology)
  services=$(jq -r '[.data.nodes[]?.serviceName // empty] | length' <<<"$map" 2>/dev/null || echo 0)
  if [ "${services:-0}" -lt 2 ]; then
    services=$(series "sf_metric:service.request.count AND sf_environment:${LAB_NAME}" | jq -r '[.results[]?.dimensions.sf_service // empty] | unique | length' 2>/dev/null || echo 0)
  fi
  list=$(agent_names)
  names=$(grep -c . <<<"$list" || true)
  http=$(grep -c '^http\.' <<<"$list" || true)
  [ "${spans:-0}" -ge 3 ] || { raw=$(trace_spans "$TRACE"); spans=$(jq -r 'if type == "array" then length elif .spans then (.spans | length) else 0 end' <<<"$raw" 2>/dev/null || echo 0); }
  echo "  t+$(( $(date +%s) - t_traffic ))s from first traffic: services=${services:-0} metric names=${names:-0} http names=${http:-0} spans in the known trace=${spans:-0}"
  [ "${services:-0}" -ge 2 ] && [ "${names:-0}" -ge 5 ] && [ "${spans:-0}" -ge 3 ] && [ "${http:-0}" -ge 1 ] && break
  sleep 20
done

if [ "${services:-0}" -lt 2 ] || [ "${spans:-0}" -lt 3 ]; then
  echo "-- collector export errors, if any"
  kubectl -n splunk logs daemonset/splunk-otel-collector-agent --tail=400 2>/dev/null | grep -iE 'error|dropping|forbidden|unauthorized' | tail -8 | cut -c1-220
fi

echo
echo "-- service map from Splunk APM"
jq -r '.data.nodes[]? | "  \(.serviceName) inferred=\(.inferred // false)"' <<<"$map" 2>/dev/null || true
jq -r '.data.edges[]? | "  \(.fromNode) -> \(.toNode)"' <<<"$map" 2>/dev/null || true
[ -n "$(jq -r '.data.nodes[]?' <<<"$map" 2>/dev/null)" ] || echo "  topology response: $(head -c 300 <<<"$map")"
echo "services map count from Splunk: ${services:-0}"
echo "HERO_MEASURE: services_on_map=${services:-null}"

echo
echo "-- agent metric names in Splunk"
agent_names | sed 's/^/  /'
echo "agent metric names count: ${names:-0}"
echo "HERO_MEASURE: agent_metric_names=${names:-null}"

echo
if [ "${spans:-0}" -lt 3 ]; then
  echo "-- the trace API gave ${spans:-0} spans: $(head -c 200 <<<"$raw")"
  echo "-- so the agent prints this trace's spans to the pod logs too, for one short run"
  for s in orders inventory; do
    kubectl -n demo set env "deployment/$s" "JAVA_TOOL_OPTIONS=-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$s -Dotel.exporter.otlp.endpoint=http://\$(HOST_IP):4318 -Dotel.resource.attributes=deployment.environment=$LAB_NAME,deployment.environment.name=$LAB_NAME -Dotel.logs.exporter=none -Dotel.traces.exporter=otlp,console" >/dev/null
  done
  kubectl -n demo rollout status deploy/inventory --timeout="$(cap 60 45)s" >/dev/null
  kubectl -n demo rollout status deploy/orders --timeout="$(cap 60 45)s" >/dev/null
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  sed -e "s/LOAD_NODE/$LOAD_NODE/" -e 's/value: "90"/value: "12"/' -e 's/value: "30"/value: "2"/' k8s/20-load.yaml | kubectl apply -f - >/dev/null
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 40 15)s" >/dev/null
  TRACE=$(kubectl -n demo logs load | grep -oE 'last_trace_id=[0-9a-f]{32}' | cut -d= -f2)
  sleep 6
  for s in orders inventory; do kubectl -n demo logs "$(newest "$s")" -c app 2>/dev/null; done | grep LoggingSpanExporter | grep "${TRACE:-none}" | sed -E 's/^.*LoggingSpanExporter - //' | cut -c1-110 > trace-spans.txt
  cat trace-spans.txt
  spans=$(grep -c . trace-spans.txt || true)
else
  echo "-- trace $TRACE from Splunk APM"
  jq -r 'if type == "array" then .[] else .spans[]? end | "  \(.serviceName) \(.kind) \(.operationName)"' <<<"$raw" 2>/dev/null | head -12
fi
echo "spans per request in one trace: ${spans:-0}"
echo "HERO_MEASURE: spans_per_request=${spans:-null}"

echo
echo "== 7. The cost, on the same jar, image, nodes and load"
median() { grep "^$2 " "$1" | awk '{print $2}' | sort -n | sed -n 2p; }
for s in orders inventory; do
  b=$(median startup-before.txt "$s")
  a=$(median startup-after.txt "$s")
  added=$(awk -v a="$a" -v b="$b" 'BEGIN{printf "%.2f", a-b}')
  echo "$s median startup without the agent ${b}s, with the agent ${a}s, added $added"
  echo "HERO_MEASURE: startup_added_seconds[$s]=$added"
done
for s in orders inventory; do
  b=$(awk -v s="$s" '$1==s{print $2}' memory-before.txt)
  a=$(awk -v s="$s" '$1==s{print $2}' memory-after.txt)
  added=$(awk -v a="$a" -v b="$b" 'BEGIN{printf "%.1f", (a-b)/1024}')
  echo "$s resident memory without the agent $b kB, with the agent $a kB, added $added MiB"
  echo "HERO_MEASURE: memory_added_mib[$s]=$added"
done
b=$(grep -oE 'p95_ms=[0-9.]+' load-before.txt | cut -d= -f2)
a=$(grep -oE 'p95_ms=[0-9.]+' load-after.txt | cut -d= -f2)
grep -h 'LOAD requests' load-before.txt load-after.txt
added=$(awk -v a="$a" -v b="$b" 'BEGIN{printf "%.2f", a-b}')
echo "p95 added ms between the runs: $added"
echo "HERO_MEASURE: p95_added_ms=$added"

if [ "${services:-0}" -ge 2 ] && [ "${names:-0}" -ge 5 ] && [ "${spans:-0}" -ge 3 ]; then
  emit fixed
else
  emit not_fixed
fi
