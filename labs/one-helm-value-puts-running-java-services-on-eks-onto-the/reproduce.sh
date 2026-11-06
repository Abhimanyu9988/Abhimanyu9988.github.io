#!/usr/bin/env bash
# BEFORE: orders calls inventory across two nodes, and inventory reads Postgres. The Collector runs, and OBI is off.
# These pods stay up. fix.sh has to put them on the Splunk APM map without touching them.
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

source ./lib.sh
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"

# EKS gets a third node for the load. The free rehearsal has two, so the load shares a node there.
want=$(terraform output -raw node_count 2>/dev/null)
[[ "$want" =~ ^[0-9]+$ ]] || want=2
echo "== 1. Waiting for $want worker nodes"
ready=0
t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt 300 ]; do
  ready=$(kubectl --request-timeout=15s get nodes --no-headers 2>/dev/null | grep -c ' Ready' || true)
  [ "${ready:-0}" -ge "$want" ] && break
  sleep 10
done
kubectl --request-timeout=15s get nodes -o custom-columns='NAME:.metadata.name,ARCH:.status.nodeInfo.architecture,KERNEL:.status.nodeInfo.kernelVersion,OS:.status.nodeInfo.osImage,RUNTIME:.status.nodeInfo.containerRuntimeVersion' || true
[ "${ready:-0}" -ge "$want" ] || fail "nodes did not become Ready"
kubectl -n kube-system rollout status deploy/coredns --timeout="$(cap 90 500)s" || true

# orders and inventory run on different nodes, so a trace can only join by crossing the network, as it would in a real cluster.
ORDERS_NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
INVENTORY_NODE=$(kubectl get nodes -o jsonpath='{.items[1].metadata.name}')
LOAD_NODE=$(kubectl get nodes -o jsonpath='{.items[2].metadata.name}')
[ -n "$LOAD_NODE" ] || LOAD_NODE=$INVENTORY_NODE
echo "$LOAD_NODE" > load-node.txt
echo "orders node: $ORDERS_NODE"
echo "inventory and postgres node: $INVENTORY_NODE"
echo "load node: $LOAD_NODE"

[ -n "${SPLUNK_REALM:-}" ] || fail "SPLUNK_REALM is not set"
[ -n "${SPLUNK_ACCESS_TOKEN:-}" ] || fail "SPLUNK_ACCESS_TOKEN is not set"

echo "== 2. Installing the Splunk OTel Collector (chart 0.163.1) with OBI at its default, off"
helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart >/dev/null 2>&1
helm repo update >/dev/null 2>&1
helm upgrade --install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version 0.163.1 --namespace splunk --create-namespace \
  --set splunkObservability.realm="$SPLUNK_REALM" \
  --set splunkObservability.accessToken="$SPLUNK_ACCESS_TOKEN" \
  --set clusterName="$LAB_NAME" \
  --set environment="$LAB_NAME" \
  --set cloudProvider=aws \
  --set distribution=eks \
  >/dev/null || fail "collector install failed"
kubectl -n splunk rollout status daemonset/splunk-otel-collector-agent --timeout="$(cap 150 500)s" || fail "collector agent not ready"
echo "OBI pods running: $(kubectl -n splunk get pods -l app.kubernetes.io/name=obi --no-headers 2>/dev/null | grep -c . || true)"

echo "== 2b. Pulling the OBI image onto every node, so its pull can't eat into the measured stages"
OBI_IMAGE=$(helm template lab splunk-otel-collector-chart/splunk-otel-collector --version 0.163.1 --set splunkObservability.realm=us0 --set splunkObservability.accessToken=not-a-token --set clusterName=pull --set obi.enabled=true 2>/dev/null | grep -m1 -oE 'ghcr\.io/[^ "]*ebpf-instrument:[^ "]+')
if [ -n "$OBI_IMAGE" ]; then
  n=0
  for node in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}'); do
    n=$(( n + 1 ))
    sed -e "s/POD_NAME/pull-$n/" -e "s/NODE_NAME/$node/" -e "s|OBI_IMAGE|$OBI_IMAGE|" k8s/05-prepull.yaml | kubectl apply -f - >/dev/null || fail "could not start the image pull"
  done
  have=0
  for i in $(seq 1 48); do
    have=$(kubectl -n splunk get pods -o json | jq '[.items[] | select(.metadata.name | test("^pull-")) | select(.status.containerStatuses[0].imageID // "" | length > 0)] | length')
    [ "${have:-0}" -ge "$n" ] && break
    sleep 5
  done
  echo "OBI image $OBI_IMAGE on $have of $n nodes"
  kubectl -n splunk get pods -o name | grep '/pull-' | xargs -r kubectl -n splunk delete --wait=false >/dev/null 2>&1
else
  echo "could not read the OBI image from the chart, so OBI will pull it when it starts"
fi

echo "== 3. Building the jar once on each node"
for n in a b; do
  node=$ORDERS_NODE
  [ "$n" = b ] && node=$INVENTORY_NODE
  sed -e "s/BUILD_NAME/build-$n/" -e "s/BUILD_NODE/$node/" k8s/00-build.yaml | kubectl apply -f - >/dev/null || fail "could not start the build"
done
done_jobs=0
t0=$(date +%s)
limit=$(cap 430 330)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  kubectl -n demo get jobs -o jsonpath='{.items[*].status.conditions[?(@.status=="True")].type}' 2>/dev/null | grep -q Failed && break
  done_jobs=$(kubectl -n demo get jobs -o jsonpath='{.items[*].status.conditions[?(@.status=="True")].type}' 2>/dev/null | grep -o Complete | grep -c . || true)
  [ "${done_jobs:-0}" -ge 2 ] && break
  sleep 10
done
kubectl -n demo logs job/build-a --tail=5 2>/dev/null || true
[ "${done_jobs:-0}" -ge 2 ] || { kubectl -n demo describe jobs | tail -15; fail "the builds did not finish"; }
echo "both builds took $(( $(date +%s) - t0 ))s"

echo "== 4. Deploying Postgres, orders and inventory"
sed -e "s/ORDERS_NODE/$ORDERS_NODE/" -e "s/INVENTORY_NODE/$INVENTORY_NODE/" k8s/10-app.yaml | kubectl apply -f - >/dev/null || fail "could not deploy the app"
kubectl -n demo rollout status deploy/postgres --timeout="$(cap 90 240)s" || fail "postgres did not start"
kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 220)s" || fail "inventory did not start"
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 210)s" || fail "orders did not start"

echo "== 5. The JVMs before OBI"
for s in orders inventory; do
  p=$(newest "$s")
  echo "$s image: $(kubectl -n demo get "$p" -o jsonpath='{.spec.containers[0].image}')"
  echo "$s JAVA_TOOL_OPTIONS or OTEL settings: $(kubectl -n demo get "$p" -o json | jq '[.spec.containers[0].env[].name | select(test("^(JAVA_TOOL_OPTIONS|OTEL_)"))] | length')"
  loaded=$(kubectl -n demo exec "$p" -c app -- grep -c libinstrument /proc/1/maps 2>/dev/null || true)
  echo "$s Java agents loaded: ${loaded:-0}"
  echo "HERO_MEASURE: jvm_helper_before[$s]=$(( ${loaded:-0} > 0 ))"
done

echo "== 6. Warming the JVMs, then five rounds with OBI off, ending seconds before the change in fix.sh"
: > load-warmup.txt
for round in 1 2; do load_round load-warmup.txt; done
grep -q 'errors=0' load-warmup.txt || fail "requests failed before any change"
: > load-off.txt
for n in 1 2 3 4 5; do round load-off.txt OFF; done

echo "== 7. The pods as they stand: name, UID, restarts, container start time, image"
pods_now | tee pods-before.txt
[ "$(grep -c . pods-before.txt)" -eq 3 ] || fail "expected 3 pods"

emit reproduced
