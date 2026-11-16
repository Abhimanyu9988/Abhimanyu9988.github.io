#!/usr/bin/env bash
# BEFORE: a Flink 1.20 streaming job on EKS, with the Splunk OTel Collector on the cluster but no Flink receiver. Two
# TaskManager pods are deleted, one at a time. Flink restarts the job each time and reports RUNNING again. We record
# what the Collector's Kubernetes metrics in Splunk show for the same window (TaskManager container restarts with
# rollup='max', available replicas with rollup='min', so a dip or rise inside a 10 s point is kept) next to what
# Kubernetes itself reported once a second (kubectl). The Collector's own scrape interval still applies.
set -uo pipefail
cd "$(dirname "$0")"
. ./lib.sh

start_clock 840 not_reproduced
aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"

echo "== 1. Waiting for 2 worker nodes"
ready=0; t0=$(date +%s)
while [ $(( $(date +%s) - t0 )) -lt 300 ]; do
  ready=$(kubectl --request-timeout=15s get nodes --no-headers 2>/dev/null | grep -c ' Ready' || true)
  [ "${ready:-0}" -ge 2 ] && break
  sleep 10
done
[ "${ready:-0}" -ge 2 ] || fail "nodes did not become Ready"
kubectl -n kube-system rollout status deploy/coredns --timeout="$(cap 90 600)s" >/dev/null || true
APP_NODE=$(kubectl get nodes -l '!node-role.kubernetes.io/control-plane' -o jsonpath='{.items[0].metadata.name}')   # Flink is scheduled, so never a tainted node
LOAD_NODE=$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | grep -vx "$APP_NODE" | head -1)
echo "$APP_NODE" > app-node.txt; echo "$LOAD_NODE" > load-node.txt
echo "Flink on $APP_NODE; the watcher on $LOAD_NODE"

[ -n "${SPLUNK_REALM:-}" ] && [ -n "${SPLUNK_ACCESS_TOKEN:-}" ] || fail "the Splunk settings are not set"
echo "== 2. Installing the Splunk OTel Collector (chart 0.163.1)"
helm repo add splunk-otel-collector-chart https://signalfx.github.io/splunk-otel-collector-chart >/dev/null 2>&1
helm repo update >/dev/null 2>&1
helm upgrade --install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version 0.163.1 --namespace splunk --create-namespace \
  --set splunkObservability.realm="$SPLUNK_REALM" --set splunkObservability.accessToken="$SPLUNK_ACCESS_TOKEN" \
  --set clusterName="$LAB_NAME" --set cloudProvider=aws --set distribution=eks >/dev/null || fail "collector install failed"
kubectl -n splunk rollout status daemonset/splunk-otel-collector-agent --timeout="$(cap 150 560)s" >/dev/null || fail "collector agent not ready"

echo "== 3. Flink 1.20 session cluster and the StateMachine example job (checkpoints every 5 s)"
sed "s/APP_NODE/$APP_NODE/g" k8s/10-app.yaml | kubectl apply -f - >/dev/null || fail "could not deploy Flink"
kubectl -n demo rollout status deploy/flink-jobmanager --timeout="$(cap 240 420)s" >/dev/null || { why; fail "the JobManager did not start"; }
kubectl -n demo rollout status deploy/flink-taskmanager --timeout="$(cap 120 400)s" >/dev/null || { why; fail "the TaskManager did not start"; }
start_watch watch-baseline 420
kubectl -n demo exec deploy/flink-jobmanager -- flink run -d -p 2 /opt/flink/examples/streaming/StateMachineExample.jar 2>&1 | grep -m1 -i "jobid" || { why; fail "the job was not submitted"; }
wait_running "$(cap 120 300)" 0 || { why; fail "the job did not reach RUNNING"; }
echo "image $(kubectl -n demo get deploy flink-jobmanager -o jsonpath='{.spec.template.spec.containers[0].image}'), $(kubectl -n demo logs "$WATCH" | head -1)"
sleep 20                                            # a steady start, so the first kill isn't mixed with the job's start

echo "== 4. Deleting the TaskManager pod twice, 30 s apart"
: > kills.txt; : > kube.txt
( while :; do echo "KUBE t=$(date +%s) available=$(kubectl -n demo get deploy flink-taskmanager -o jsonpath='{.status.availableReplicas}') pods=$(kubectl -n demo get pods -l app=flink-taskmanager -o jsonpath='{range .items[*]}{.metadata.name}|{.status.conditions[?(@.type=="Ready")].status}|{.status.conditions[?(@.type=="Ready")].lastTransitionTime}|{.status.containerStatuses[0].restartCount}{" "}{end}')" >> kube.txt; sleep 1; done ) &
KUBE=$!
sleep 3                                              # kubectl samples from before the first deletion
w0=$(date +%s)
kill_tm baseline "$(cap 120 200)"; sleep 30
kill_tm baseline "$(cap 120 150)"; sleep 20
w1=$(date +%s)
kill "$KUBE" 2>/dev/null
report baseline "$w0" "$w1"
rest=$(measure flink_rest_restarts baseline)

echo "== 5. The same window in Splunk Observability Cloud's Kubernetes metrics"
wait_cluster "$(cap 120 60)"
[ "${cluster:-0}" -gt 0 ] || fail "Splunk shows nothing from this cluster"
sleep "$(cap 60 40)"                                 # the last 10 s points arrive
C="filter('k8s.cluster.name', '$LAB_NAME')"
counter k8s.container.restarts "$C and filter('k8s.container.name', 'taskmanager')" $(( w0 - 30 )) "$w1" sf-k8s-restarts.txt "" max
counter k8s.deployment.available "$C and filter('k8s.deployment.name', 'flink-taskmanager')" $(( w0 - 30 )) "$w1" sf-k8s-available.txt "" min
echo "  metric types: k8s.container.restarts $(metric_type k8s.container.restarts), k8s.deployment.available $(metric_type k8s.deployment.available)"
echo "  k8s.container.restarts (taskmanager) per 10 s: $(awk '{printf "%s=%s ", $1, $2}' sf-k8s-restarts.txt)"
echo "  k8s.deployment.available (flink-taskmanager) per 10 s: $(awk '{printf "%s=%s ", $1, $2}' sf-k8s-available.txt)"
pods=$(series "sf_metric:k8s.container.restarts AND k8s.cluster.name:${LAB_NAME} AND k8s.container.name:taskmanager" | jq -r '[.results[]?.dimensions["k8s.pod.name"]] | unique | length' 2>/dev/null)
rmax=$(awk 'BEGIN {m = "null"} m == "null" || $2 > m {m = $2} END {print m}' sf-k8s-restarts.txt)
amin=$(awk 'BEGIN {m = "null"} m == "null" || $2 < m {m = $2} END {print m}' sf-k8s-available.txt)
echo "TaskManager pods Splunk has a container series for: ${pods:-0}; highest container restart count ${rmax}; lowest available replicas over the window ${amin}"
echo "  kube.txt, kubectl once a second: $(wc -l < kube.txt | tr -d ' ') samples; first: $(head -1 kube.txt | cut -c1-300)"
for f in kube.txt sf-k8s-available.txt; do kubectl cp "$f" "demo/analyzer:/tmp/$f" >/dev/null 2>&1 || fail "could not copy $f to the analyzer"; done
kubectl cp kills.txt demo/analyzer:/tmp/kills.txt >/dev/null 2>&1
kubectl -n demo exec analyzer -- bash /tmp/analyze.sh k8s baseline /tmp/kube.txt /tmp/kills.txt /tmp/sf-k8s-available.txt | tee report-k8s.txt
echo "  the chart's k8s_cluster receiver settings: $(kubectl -n splunk get cm -o yaml 2>/dev/null | awk '/k8s_cluster:/ {f = 1} f && /collection_interval/ {print; exit}' | tr -s ' ')"
echo "  (no collection_interval line means the receiver default, 10s: one report per 10 s)"
echo "HERO_MEASURE: splunk_k8s_view[taskmanager_pod_series]=${pods:-0}"
echo "HERO_MEASURE: splunk_k8s_view[container_restarts_max]=$rmax"
names=$(flink_names | wc -l | tr -d ' ')
echo "flink.* metric names for deployment.environment=$LAB_NAME: $names (no Flink receiver runs in this phase)"
[ "${rest:-0}" -ge 2 ] || fail "expected 2 restarts in Flink's REST API"
[ -s sf-k8s-restarts.txt ] && [ -s sf-k8s-available.txt ] || fail "Splunk returned no TaskManager Kubernetes metrics for the window"
emit reproduced
