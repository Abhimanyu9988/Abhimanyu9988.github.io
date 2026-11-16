#!/usr/bin/env bash
# BEFORE: instant retries. orders calls inventory with a 500 ms timeout and retries a failed call at once, up to 3 more
# times. Open-loop load of 40 orders a second; at second 20 inventory slows to 2 s a request for 20 s, then is healthy.
# Sets up the cluster, then runs naive-1, budget-1 and naive-2. fix.sh runs budget-2, naive-3 and budget-3.
set -uo pipefail
cd "$(dirname "$0")"
. ./lib.sh

start_clock 810 not_reproduced

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

# The services stay on one node and the load on the other, in both runs.
APP_NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
LOAD_NODE=$(kubectl get nodes -o jsonpath='{.items[1].metadata.name}')
echo "$APP_NODE" > app-node.txt
echo "$LOAD_NODE" > load-node.txt
echo "app node: $APP_NODE"
echo "load node: $LOAD_NODE"

[ -n "${SPLUNK_REALM:-}" ] && [ -n "${SPLUNK_ACCESS_TOKEN:-}" ] || fail "the Splunk settings are not set"

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

echo "== 3. Building one virtualenv with pinned packages on the app node"
sed "s/APP_NODE/$APP_NODE/" k8s/00-build.yaml | kubectl apply -f - >/dev/null || fail "could not start the build"
state=""
t0=$(date +%s)
limit=$(cap 360 330)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  state=$(kubectl -n demo get job build -o jsonpath='{.status.conditions[?(@.status=="True")].type}' 2>/dev/null)
  case "$state" in *Complete*|*Failed*) break ;; esac
  sleep 10
done
kubectl -n demo logs job/build --tail=20 2>/dev/null || true
case "$state" in *Complete*) ;; *) kubectl -n demo describe job build | tail -15; fail "the build did not finish: ${state:-still running}" ;; esac
echo "build took $(( $(date +%s) - t0 ))s"

echo "== 4. Deploying inventory and orders with zero-code OpenTelemetry"
sed -e "s/APP_NODE/$APP_NODE/" -e "s/LAB_ENV/$LAB_NAME/g" k8s/10-app.yaml | kubectl apply -f - >/dev/null || fail "could not deploy the services"
kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 290)s" >/dev/null || { why inventory; fail "inventory did not start"; }
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 280)s" >/dev/null || { why orders; fail "orders did not start"; }

echo "== 5. Run naive-1: instant retries, the slowdown at second 20, then 70 s to watch"
start_run naive-1 120
load_run naive-1 "$(cap 200 330)"
report naive-1
[ "${failed_after:-0}" -gt 0 ] || fail "no order failed after the slowdown, so there is nothing to compare"

echo "== 6. Run budget-1: backoff with full jitter and a retry budget, same load and slowdown"
start_run budget-1 120
load_run budget-1 "$(cap 200 170)"
report budget-1

echo "== 7. Run naive-2: instant retries again"
start_run naive-2 120
load_run naive-2 "$(cap 200 15)"
report naive-2

emit reproduced
