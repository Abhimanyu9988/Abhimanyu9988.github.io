#!/usr/bin/env bash
# BEFORE: a Spring Boot orders service calls an inventory service, which reads Postgres. The Collector runs, and no agent is attached.
# This fixes the baseline: startup, resident memory and p95 latency, on the jar and image the agent run reuses.
set -uo pipefail
cd "$(dirname "$0")"

START=$(date +%s)
LIMIT=810
HERO_DONE=0
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit not_reproduced; exit 0; }
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

[ -n "${SPLUNK_REALM:-}" ] || fail "SPLUNK_REALM is not set"
[ -n "${SPLUNK_ACCESS_TOKEN:-}" ] || fail "SPLUNK_ACCESS_TOKEN is not set"

echo "== 2. Installing the Splunk OTel Collector (chart 0.163.1) first, so it is running in both measurements"
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

echo "== 3. Building the jar in the cluster, once, and fetching the agent, unused for now"
sed "s/APP_NODE/$APP_NODE/" k8s/00-build.yaml | kubectl apply -f - >/dev/null || fail "could not start the build"
state=""
t0=$(date +%s)
limit=$(cap 430 300)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  state=$(kubectl -n demo get job build -o jsonpath='{.status.conditions[?(@.status=="True")].type}' 2>/dev/null)
  case "$state" in *Complete*|*Failed*) break ;; esac
  sleep 10
done
kubectl -n demo logs job/build --all-containers --tail=20 2>/dev/null || true
case "$state" in *Complete*) ;; *) kubectl -n demo describe job build | tail -15; fail "the build did not finish: ${state:-still running}" ;; esac
echo "build took $(( $(date +%s) - t0 ))s"

echo "== 4. Deploying Postgres, orders and inventory with no agent"
sed "s/APP_NODE/$APP_NODE/" k8s/10-app.yaml | kubectl apply -f - >/dev/null || fail "could not deploy the app"
kubectl -n demo rollout status deploy/postgres --timeout="$(cap 90 200)s" || fail "postgres did not start"
kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 180)s" || fail "inventory did not start"
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 170)s" || fail "orders did not start"

newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }

startup() {   # seconds from JVM start to "Started App", read from Spring Boot's own log line
  local pod line="" i
  pod=$(newest "$1")
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    line=$(kubectl -n demo logs "$pod" -c app 2>/dev/null | grep -oE 'process running for [0-9.]+' | tail -1)
    [ -n "$line" ] && break
    sleep 3
  done
  echo "${line##* }"
}

echo "== 5. No agent is attached, and the image and jar are recorded"
for s in orders inventory; do
  p=$(newest "$s")
  echo "$s image: $(kubectl -n demo get "$p" -o jsonpath='{.spec.containers[0].image}')"
  echo "$s JAVA_TOOL_OPTIONS or OTEL settings: $(kubectl -n demo get "$p" -o json | jq '[.spec.containers[0].env[].name | select(test("^(JAVA_TOOL_OPTIONS|OTEL_)"))] | length')"
  echo "$s jar: $(kubectl -n demo exec "$p" -c app -- sha256sum /lab/app.jar)"
done
kubectl -n demo exec "$(newest orders)" -c app -- sha256sum /lab/app.jar | awk '{print $1}' > jar-sha.txt

echo "== 6. Startup, three restarts of each service"
: > startup-before.txt
for round in 1 2 3; do
  kubectl -n demo rollout restart deploy/inventory deploy/orders >/dev/null || fail "restart failed"
  kubectl -n demo rollout status deploy/inventory --timeout="$(cap 90 130)s" >/dev/null || fail "inventory did not restart"
  kubectl -n demo rollout status deploy/orders --timeout="$(cap 90 130)s" >/dev/null || fail "orders did not restart"
  for s in orders inventory; do
    v=$(startup "$s")
    [ -n "$v" ] || fail "no startup line for $s"
    echo "$s startup round $round: process running for $v"
    echo "$s $v" >> startup-before.txt
  done
done

echo "== 7. The same load as the agent run: one request every 50 ms, the first 30 seconds as warm-up"
kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
sed "s/LOAD_NODE/$LOAD_NODE/" k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 170 15)s" || { kubectl -n demo logs load --tail=20; fail "the load did not finish"; }
kubectl -n demo logs load | tee load-before.txt
grep -q 'p95_ms=' load-before.txt || fail "no latency result"

echo "== 8. Resident memory after the load"
: > memory-before.txt
for s in orders inventory; do
  v=$(kubectl -n demo exec "$(newest "$s")" -c app -- grep VmRSS /proc/1/status | awk '{print $2}')
  [ -n "$v" ] || fail "no memory value for $s"
  echo "$s VmRSS: $v kB"
  echo "$s $v" >> memory-before.txt
done

emit reproduced
