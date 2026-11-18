#!/usr/bin/env bash
# BEFORE: the gateway Collector forwards spans as they arrive (base.yaml, batch only). Every order carries a customer
# token in its query string, and zero-code OpenTelemetry records the URL on each span under both the current and the older
# HTTP attribute names, so the token reaches Splunk APM in 3 spans per order. The kubelet's /healthz checks are traced
# too. Sets up the cluster, runs before-1 and before-2, and reads before-1's sample traces back from Splunk by trace ID.
# fix.sh runs the two OTTL configs.
set -uo pipefail
cd "$(dirname "$0")"
. ./lib.sh
. ./splunk.sh

start_clock 820 not_reproduced

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
for v in SPLUNK_REALM SPLUNK_ACCESS_TOKEN SPLUNK_API_TOKEN; do [ -n "${!v:-}" ] || fail "$v is not set"; done

echo "== 1. Waiting for 2 worker nodes"
ready=0
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt 300 ]; do
  ready=$(kubectl --request-timeout=15s get nodes --no-headers 2>/dev/null | grep -c ' Ready' || true)
  [ "${ready:-0}" -ge 2 ] && break
  sleep 10
done
kubectl --request-timeout=15s get nodes --no-headers -o custom-columns=NAME:.metadata.name,TYPE:.metadata.labels.node\.kubernetes\.io/instance-type,READY:.status.conditions[-1].type || true
[ "${ready:-0}" -ge 2 ] || fail "nodes did not become Ready"
kubectl -n kube-system rollout status deploy/coredns --timeout="$(cap 90 600)s" >/dev/null || true

# The services and the gateway stay on one node and the load on the other, in every run.
APP_NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
LOAD_NODE=$(kubectl get nodes -o jsonpath='{.items[1].metadata.name}')
echo "$APP_NODE" > app-node.txt
echo "$LOAD_NODE" > load-node.txt
echo "app node: $APP_NODE; load node: $LOAD_NODE"

echo "== 2. Installing the Splunk OTel Collector (chart 0.163.1), the same in every run"
for i in 1 2 3; do
  helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart >/dev/null 2>&1 && helm repo update >/dev/null 2>&1 && break
  sleep 10
done
helm upgrade --install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version 0.163.1 --namespace splunk --create-namespace \
  --set splunkObservability.realm="$SPLUNK_REALM" \
  --set splunkObservability.accessToken="$SPLUNK_ACCESS_TOKEN" \
  --set clusterName="$LAB_NAME" \
  --set cloudProvider=aws \
  --set distribution=eks \
  >/dev/null || fail "collector install failed"
kubectl -n splunk rollout status daemonset/splunk-otel-collector-agent --timeout="$(cap 150 560)s" >/dev/null || fail "collector agent not ready"
echo "collector agent ready on $(kubectl -n splunk get daemonset splunk-otel-collector-agent -o jsonpath='{.status.numberReady}') nodes"

echo "== 3. Building one virtualenv with pinned packages on the app node"
sed "s/APP_NODE/$APP_NODE/" k8s/00-build.yaml | kubectl apply -f - >/dev/null || fail "could not start the build"
state=""
t0=$(date +%s)
limit=$(cap 360 400)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  state=$(kubectl -n demo get job build -o jsonpath='{.status.conditions[?(@.status=="True")].type}' 2>/dev/null)
  case "$state" in *Complete*|*Failed*) break ;; esac
  sleep 10
done
kubectl -n demo logs job/build --tail=12 2>/dev/null | grep -v '^\[notice\]' | cut -c1-300 | tail -10 || true
case "$state" in *Complete*) ;; *) kubectl -n demo describe job build | tail -15; fail "the build did not finish: ${state:-still running}" ;; esac
echo "build took $(( $(date +%s) - t0 ))s"

echo "== 4. Deploying the gateway Collector (contrib 0.165.0), inventory and orders with zero-code OpenTelemetry"
sed -e "s/APP_NODE/$APP_NODE/" -e "s/LOAD_NODE/$LOAD_NODE/" k8s/30-gateway.yaml | kubectl apply -f - >/dev/null || fail "could not deploy the gateway"
kubectl -n demo rollout status deploy/gateway --timeout="$(cap 120 380)s" >/dev/null || { why gateway; fail "the gateway did not start"; }
sed -e "s/APP_NODE/$APP_NODE/" -e "s/LAB_ENV/$LAB_NAME/g" k8s/10-app.yaml | kubectl apply -f - >/dev/null || fail "could not deploy the services"
kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 370)s" >/dev/null || { why inventory; fail "inventory did not start"; }
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 360)s" >/dev/null || { why orders; fail "orders did not start"; }

rm -f periods.txt
echo "== 5. Run before-1: the gateway with base.yaml (batch only)"
start_run before-1 120
load_run before-1 "$(cap 100 300)"
measures before-1

echo "== 6. Run before-2: the same again"
start_run before-2 120
load_run before-2 "$(cap 100 200)"
measures before-2

echo "== 7. before-1's sample traces, read back from Splunk APM by trace ID"
read_traces_patiently before-1 "$(cap 240 15)"
# fix.sh reads every run again and records the measures, so here the counts are only the reason to go on.
[ "$found" -gt 0 ] || fail "no before-1 sample trace reached Splunk APM, so there is nothing to show"
[ "$raw" -gt 0 ] || fail "Splunk holds before-1's traces but no span shows the token, so there is nothing to fix"

emit reproduced
