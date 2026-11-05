#!/usr/bin/env bash
# BEFORE: orders sends each order to an SQS queue and fulfilment handles it with @SqsListener. The Collector runs and no agent is attached.
# This fixes the baseline: every message arrives, and the message itself carries no trace header.
set -uo pipefail
cd "$(dirname "$0")"
. ./lib.sh

start_clock 810 not_reproduced

aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)" --region "$AWS_REGION" >/dev/null || fail "could not write kubeconfig"
queue_settings
[ -n "$QUEUE_URL" ] || fail "no queue URL"

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

# The services stay on one node and the load on the other, in every run.
APP_NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
LOAD_NODE=$(kubectl get nodes -o jsonpath='{.items[1].metadata.name}')
echo "$APP_NODE" > app-node.txt
echo "$LOAD_NODE" > load-node.txt
echo "app node: $APP_NODE"
echo "load node: $LOAD_NODE"

[ -n "${SPLUNK_REALM:-}" ] || fail "SPLUNK_REALM is not set"
[ -n "${SPLUNK_ACCESS_TOKEN:-}" ] || fail "SPLUNK_ACCESS_TOKEN is not set"

echo "== 2. Installing the Splunk OTel Collector (chart 0.163.1) first, so it is running in every run"
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

echo "== 4. Deploying orders and fulfilment with no agent"
if standin; then
  echo "free rehearsal: ElasticMQ stands in for Amazon SQS"
  sed "s/LOAD_NODE/$LOAD_NODE/" k8s/15-standin-sqs.yaml | kubectl apply -f - >/dev/null || fail "could not start the stand-in"
  kubectl -n demo rollout status deploy/sqs --timeout="$(cap 90 180)s" || fail "the stand-in did not start"
  extra=(--from-literal=SPRING_CLOUD_AWS_SQS_ENDPOINT=http://sqs:9324 --from-literal=AWS_ACCESS_KEY_ID=standin --from-literal=AWS_SECRET_ACCESS_KEY=<redacted>
else
  extra=()
  echo "queue: $QUEUE_NAME in $AWS_REGION"
fi
kubectl -n demo create configmap sqs-env --from-literal=QUEUE_NAME="$QUEUE_NAME" --from-literal=AWS_REGION="$AWS_REGION" --from-literal=SPRING_CLOUD_AWS_SQS_LISTENER_MAX_MESSAGES_PER_POLL=10 "${extra[@]}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null || fail "could not save the queue settings"
sed "s/APP_NODE/$APP_NODE/" k8s/10-app.yaml | kubectl apply -f - >/dev/null || fail "could not deploy the services"
kubectl -n demo rollout status deploy/fulfilment --timeout="$(cap 120 170)s" || { why fulfilment; fail "fulfilment did not start"; }
kubectl -n demo rollout status deploy/orders --timeout="$(cap 120 160)s" || { why orders; fail "orders did not start"; }

echo "== 5. No agent is attached, and the image and jar are recorded"
for s in orders fulfilment; do
  p=$(newest "$s")
  echo "$s image: $(kubectl -n demo get "$p" -o jsonpath='{.spec.containers[0].image}')"
  echo "$s JAVA_TOOL_OPTIONS or OTEL settings: $(kubectl -n demo get "$p" -o json | jq '[.spec.containers[0].env[].name | select(test("^(JAVA_TOOL_OPTIONS|OTEL_)"))] | length')"
  echo "$s jar: $(kubectl -n demo exec "$p" -c app -- sha256sum /lab/app.jar)"
  standin || echo "$s Pod Identity credential settings on the pod: $(kubectl -n demo get "$p" -o json | jq '[.spec.containers[0].env[].name | select(. == "AWS_CONTAINER_CREDENTIALS_FULL_URI")] | length')"
done
kubectl -n demo exec "$(newest orders)" -c app -- sha256sum /lab/app.jar | awk '{print $1}' > jar-sha.txt
echo "agent jar, unused for now: $(kubectl -n demo exec "$(newest orders)" -c app -- sha256sum /lab/opentelemetry-javaagent.jar)"
echo "listener: @SqsListener takes one String, so single-message mode, polling up to 10 messages. Spring Cloud AWS observation is not enabled."

echo "== 6. 400 orders, one every 50 ms, each one message on the queue"
load_run 400 load-before.txt "$(cap 170 90)"
kubectl -n demo logs "$(newest orders)" -c app > orders-before.log 2>/dev/null
kubectl -n demo logs "$(newest fulfilment)" -c app > fulfilment-before.log 2>/dev/null
count_shared orders-before.log fulfilment-before.log
echo "no_agent run: sent $sent, fulfilled $handled, producer spans $produced, consumer spans $consumed"
echo "HERO_MEASURE: messages_sent[no_agent]=$sent"

echo "== 7. What one message carries with no agent: fulfilment pauses, one order goes in, and that message is read from the queue"
probe
with_header=$(grep -c . <<<"$header" || true)
echo "no_agent run, messages carrying the header: $with_header"
echo "HERO_MEASURE: trace_header_on_message[no_agent]=$with_header"
resume "$(cap 90 5)"

emit reproduced
