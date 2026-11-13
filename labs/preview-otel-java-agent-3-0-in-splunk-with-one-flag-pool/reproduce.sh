#!/usr/bin/env bash
# BEFORE: orders calls inventory, which reads Postgres, on EKS with the Splunk OTel Collector and the OTel Java agent
# 2.33.1 on its defaults. Records what Splunk stores: pool metric names per service, db.client.operation.duration and
# every database key on a real trace. fix.sh then runs the preview pair beside this one.
set -uo pipefail
cd "$(dirname "$0")"

START=$(date +%s)
LIMIT=810
HERO_DONE=0
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; type diagnose >/dev/null 2>&1 && diagnose; [ "$HERO_DONE" = 1 ] || emit not_reproduced; exit 0; }
trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: not_reproduced"; }' EXIT

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"

echo "== 1. Waiting for 2 worker nodes"
ready=0
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt 300 ]; do
  ready=$(kubectl --request-timeout=15s get nodes --no-headers 2>/dev/null | grep -c ' Ready' || true)
  [ "${ready:-0}" -ge 2 ] && break
  sleep 10
done
kubectl --request-timeout=15s get nodes -o wide || true
[ "${ready:-0}" -ge 2 ] || fail "nodes did not become Ready"
kubectl -n kube-system rollout status deploy/coredns --timeout="$(cap 90 500)s" || true

# The app stays on one node and the load generator on the other, in both runs.
APP_NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
LOAD_NODE=$(kubectl get nodes -o jsonpath='{.items[1].metadata.name}')
echo "$APP_NODE" > app-node.txt
echo "$LOAD_NODE" > load-node.txt
echo "app node: $APP_NODE"
echo "load node: $LOAD_NODE"

[ -n "${SPLUNK_REALM:-}" ] && [ -n "${SPLUNK_ACCESS_TOKEN:-}" ] && [ -n "${SPLUNK_API_TOKEN:-}" ] || fail "the Splunk settings are not set"
source ./lib.sh
newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }
echo "environments, new each run: default $DEFAULT_ENV, preview $PREVIEW_ENV"

echo "== 2. Installing the Splunk OTel Collector (chart 0.163.1), the same in both runs"
helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart >/dev/null 2>&1
helm repo update >/dev/null 2>&1
helm upgrade --install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version 0.163.1 --namespace splunk --create-namespace \
  --set splunkObservability.realm="$SPLUNK_REALM" \
  --set splunkObservability.accessToken="$SPLUNK_ACCESS_TOKEN" \
  --set clusterName="$LAB_NAME" \
  --set cloudProvider=aws \
  --set distribution=eks \
  >/dev/null || fail "collector install failed"
kubectl -n splunk rollout status daemonset/splunk-otel-collector-agent --timeout="$(cap 150 500)s" || fail "collector agent not ready"

echo "== 3. Building the jar in the cluster, once, and fetching the agent"
sed "s/APP_NODE/$APP_NODE/" k8s/00-build.yaml | kubectl apply -f - >/dev/null || fail "could not start the build"
state=""
t0=$(date +%s)
limit=$(cap 430 300)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  state=$(kubectl -n demo get job build -o jsonpath='{.status.conditions[?(@.status=="True")].type}' 2>/dev/null)
  case "$state" in *Complete*|*Failed*) break ;; esac
  sleep 10
done
kubectl -n demo logs job/build --all-containers --tail=5 2>/dev/null || true
case "$state" in *Complete*) ;; *) kubectl -n demo describe job build | tail -15; fail "the build did not finish: ${state:-still running}" ;; esac
echo "build: $(( $(date +%s) - t0 ))s"

echo "== 4. Deploying Postgres, orders and inventory"
sed "s/APP_NODE/$APP_NODE/" k8s/10-app.yaml | kubectl apply -f - >/dev/null || fail "could not deploy the app"
kubectl -n demo rollout status deploy/postgres --timeout="$(cap 90 200)s" || fail "postgres did not start"
kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 180)s" || fail "inventory did not start"
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 170)s" || fail "orders did not start"

echo "== 5. Attaching the agent with its defaults: no preview flag, no opt-in"
for s in orders inventory; do
  kubectl -n demo set env "deployment/$s" "JAVA_TOOL_OPTIONS=-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$s -Dotel.exporter.otlp.endpoint=http://\$(HOST_IP):4318 -Dotel.resource.attributes=deployment.environment=$DEFAULT_ENV -Dotel.logs.exporter=none" >/dev/null || fail "could not attach the agent to $s"
done
kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 170)s" >/dev/null || fail "inventory did not roll out"
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 170)s" >/dev/null || fail "orders did not roll out"
for s in orders inventory; do
  p=$(newest "$s")
  echo "$s jar: $(kubectl -n demo exec "$p" -c app -- sha256sum /lab/app.jar)"
  echo "$s OTEL_ variables on the pod: $(kubectl -n demo get "$p" -o json | jq -r '[.spec.containers[0].env[].name | select(startswith("OTEL_"))] | if length == 0 then "none" else join(" ") end')"
  kubectl -n demo logs "$p" -c app 2>/dev/null | grep -m1 'opentelemetry-javaagent - version' | sed "s/^/$s /"
done
kubectl -n demo exec "$(newest orders)" -c app -- sha256sum /lab/app.jar | awk '{print $1}' > jar-sha.txt

echo "== 6. A fixed load: one order every 50 ms for 120 seconds"
kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
sed -e "s/LOAD_NODE/$LOAD_NODE/" -e 's/value: "540"/value: "120"/' k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 160 15)s" || { kubectl -n demo logs load --tail=20; fail "the load did not finish"; }
LOAD_END=$(date +%s)
kubectl -n demo logs load | tee load-before.txt
errors=$(grep -oE 'errors=[0-9]+' load-before.txt | tail -1 | cut -d= -f2)
echo "failed requests in the default run's first load: ${errors:-missing}"
[ -n "$errors" ] || fail "the load printed no error count, so this run can't be used"
[ "$errors" -eq 0 ] || fail "the load saw errors, so this run can't be used"
load_start=$(grep -oE 'started_epoch=[0-9]+' load-before.txt | cut -d= -f2)
trace=$(grep -oE 'last_trace_id=[0-9a-f]+' load-before.txt | cut -d= -f2)
printf 'from_ms=%s\nto_ms=%s\n' "$(( load_start * 1000 ))" "$(( (LOAD_END + 120) * 1000 ))" > window-default.env

echo "== 7. What Splunk Observability Cloud stores for the default environment"
# Splunk's series API lags: wait for this load's pool metric, then one more 60-second export interval.
pool=0
t0=$(date +%s)
limit=$(cap 240 240)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  pool=$(pool_series "$DEFAULT_ENV" "$LEGACY_POOL" inventory)
  echo "  t+$(( $(date +%s) - t0 ))s after the load: pool series under $LEGACY_POOL.*: ${pool:-0}"
  [ "${pool:-0}" -gt 0 ] && break
  sleep 20
done
[ "${pool:-0}" -gt 0 ] || fail "the pool metric has not reached Splunk"
sleep 60
echo "-- database metric names in Splunk with the agent's defaults"
names_in "$DEFAULT_ENV" | tee names-default.txt | sed 's/^/  /'
legacy_min=999; new_total=0
for s in orders inventory; do
  l=$(pool_series "$DEFAULT_ENV" "$LEGACY_POOL" "$s"); n=$(pool_series "$DEFAULT_ENV" "$NEW_POOL" "$s")
  echo "default run, $s: pool series under $LEGACY_POOL.* ${l:-0}, under $NEW_POOL.* ${n:-0}"
  echo "HERO_MEASURE: default_pool_series[$s]=${l:-0}"
  [ "${l:-0}" -lt "$legacy_min" ] && legacy_min=${l:-0}
  new_total=$(( new_total + ${n:-0} ))
done
ops=$(op_series "$DEFAULT_ENV")
echo "default run: db.client.operation.duration series $ops"
echo "HERO_MEASURE: operation_duration_series[default]=${ops:-0}"

source ./window-default.env
mean=$(use_time_mean "$DEFAULT_ENV" "$LEGACY_POOL" "$from_ms" "$to_ms")
echo "mean connection use time as stored, default run, first load ($LEGACY_POOL.use_time, milliseconds): ${mean:-none}"

echo "-- every database span on trace $trace, as Splunk APM returns it (raw reply kept in trace-default.json)"
fetch_trace "$trace" trace-default.json || echo "  the trace had no database spans in Splunk yet"
db_spans trace-default.json | sed 's/^/  /'
keys=$(db_keys trace-default.json)
echo "database span keys in Splunk, default run: ${keys:-none found}"
old_k=$(count_in "$keys" "$LEGACY_KEYS"); new_k=$(count_in "$keys" "$NEW_KEYS")
echo "old keys $old_k, new keys $new_k"
echo "HERO_MEASURE: span_db_keys[default_old]=$old_k"
echo "HERO_MEASURE: span_db_keys[default_new]=$new_k"

# The agent sends old keys only (see check.sh); Splunk APM may show new-style keys beside them, so those are reported.
[ "$legacy_min" -ge 1 ] && [ "$new_total" -eq 0 ] && [ "$old_k" -ge 1 ] || { echo "the default run did not show the old pool names and old span keys"; emit not_reproduced; exit 0; }
emit reproduced
