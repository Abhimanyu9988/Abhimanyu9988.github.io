#!/usr/bin/env bash
# BEFORE: a Spring Boot orders service calls an inventory service, which reads Postgres. The Collector runs, and the
# OTel Java agent is attached with no opt-in and no trim, then the opt-in goes on alone. This records what Splunk holds at the start.
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

[ -n "${SPLUNK_REALM:-}" ] || fail "SPLUNK_REALM is not set"
[ -n "${SPLUNK_ACCESS_TOKEN:-}" ] || fail "SPLUNK_ACCESS_TOKEN is not set"
[ -n "${SPLUNK_API_TOKEN:-}" ] || fail "SPLUNK_API_TOKEN is not set"
source ./lib.sh
echo "lab name, new for every run, so Splunk holds nothing older under this environment: $LAB_NAME"

echo "== 2. Installing the Splunk OTel Collector (chart 0.163.1) first, the same in both measurements"
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
exporter_config

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
kubectl -n demo logs job/build --all-containers --tail=20 2>/dev/null || true
case "$state" in *Complete*) ;; *) kubectl -n demo describe job build | tail -15; fail "the build did not finish: ${state:-still running}" ;; esac
echo "build took $(( $(date +%s) - t0 ))s"

echo "== 4. Deploying Postgres, orders and inventory"
sed "s/APP_NODE/$APP_NODE/" k8s/10-app.yaml | kubectl apply -f - >/dev/null || fail "could not deploy the app"
kubectl -n demo rollout status deploy/postgres --timeout="$(cap 90 200)s" || fail "postgres did not start"
kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 180)s" || fail "inventory did not start"
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 170)s" || fail "orders did not start"

echo "== 5. Attaching the agent with no opt-in and no attribute trim"
# kind has no EC2 metadata, so a rehearsal sets those 8 attributes itself and carries the dimension count EKS will have.
EKS_LIKE=""
case "$LAB_NAME" in rehearsal-*) EKS_LIKE=",cloud.account.id=1,cloud.availability_zone=a,cloud.platform=aws_eks,cloud.provider=aws,cloud.region=r,host.id=i,host.image.id=a,host.type=t3.large" ;; esac
dimension_probe_send
for s in orders inventory; do
  kubectl -n demo set env "deployment/$s" "JAVA_TOOL_OPTIONS=-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$s -Dotel.exporter.otlp.endpoint=http://\$(HOST_IP):4318 -Dotel.resource.attributes=deployment.environment=$LAB_NAME$EKS_LIKE -Dotel.logs.exporter=none" >/dev/null || fail "could not attach the agent to $s"
done
kubectl -n demo rollout status deploy/inventory --timeout="$(cap 120 170)s" >/dev/null || fail "inventory did not roll out"
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 170)s" >/dev/null || fail "orders did not roll out"

newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }

: > agent-options.txt
for s in orders inventory; do
  p=$(newest "$s")
  echo "$s image: $(kubectl -n demo get "$p" -o jsonpath='{.spec.containers[0].image}')"
  echo "$s jar: $(kubectl -n demo exec "$p" -c app -- sha256sum /lab/app.jar)"
  echo "$s semconv opt-in settings: $(kubectl -n demo get "$p" -o json | jq '[.spec.containers[0].env[].name | select(. == "OTEL_SEMCONV_STABILITY_OPT_IN")] | length')"
  kubectl -n demo get "$p" -o json | jq -r '.spec.containers[0].env[] | select(.name == "JAVA_TOOL_OPTIONS") | .value' >> agent-options.txt
  kubectl -n demo logs "$p" -c app 2>/dev/null | grep -m1 'opentelemetry-javaagent - version' | sed "s/^/$s /"
done
kubectl -n demo exec "$(newest orders)" -c app -- sha256sum /lab/app.jar | awk '{print $1}' > jar-sha.txt
kubectl -n demo get "$(newest inventory)" -o json | jq -r '.spec.containers[0].env[].name' | sort > env-before.txt

echo "== 6. A fixed load: one request every 50 ms for 120 seconds, which is enough to show what arrives"
kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
sed -e "s/LOAD_NODE/$LOAD_NODE/" -e 's/value: "540"/value: "120"/' k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$(cap 150 15)s" || { kubectl -n demo logs load --tail=20; fail "the load did not finish"; }
kubectl -n demo logs load | tee load-before.txt
grep -q 'errors=0' load-before.txt || fail "the load saw errors, so this run can't be used"

echo "== 7. What Splunk Observability Cloud holds for this environment"
# The zero below only means something after the load's own metrics have arrived, so this waits for a pool metric from the
# same load and export cycle, then one more 60-second export interval. It is well under 36 dimensions, and HTTP may not be.
pool=0
t0=$(date +%s)
limit=$(cap 200 300)
while [ $(( $(date +%s) - t0 )) -lt "$limit" ]; do
  pool=$(series "sf_metric:db.client.connections.use_time_count AND deployment.environment:${LAB_NAME} AND service.name:inventory" | jq -r '.results | length' 2>/dev/null || echo 0)
  echo "  t+$(( $(date +%s) - t0 ))s after the load: connection pool metric series=${pool:-0}"
  [ "${pool:-0}" -gt 0 ] && break
  sleep 20
done
[ "${pool:-0}" -gt 0 ] || fail "the agent's pool metric has not reached Splunk, so this before-state can't be read"
sleep 60
report_families
dimension_probe_read
echo "-- database metrics in Splunk with no opt-in"
raw_names "sf_metric:db.* AND deployment.environment:${LAB_NAME}" | tee names-before.txt | sed 's/^/  /'
before=$(duration_series | jq -r '.results | length' 2>/dev/null || echo 0)
echo "time series for $METRIC with no opt-in: ${before:-0}"
echo "connection pool metric series for the same load: $pool"
echo "db duration series before: ${before:-0}"
echo "HERO_MEASURE: db_duration_series_before=${before:-0}"
held=$(grep -oE 'probe\.[0-9]+' <<<"${got:-}" | cut -d. -f2 | sort -n | tail -1)
echo "trim evidence: probe_largest_held ${held:-0}"
echo "HERO_MEASURE: trim_evidence[probe_largest_held]=${held:-0}"

[ "${before:-0}" -eq 0 ] || { emit not_reproduced; exit 0; }
echo "== 8. The opt-in alone, with no trim"
opt_in_alone
emit reproduced
