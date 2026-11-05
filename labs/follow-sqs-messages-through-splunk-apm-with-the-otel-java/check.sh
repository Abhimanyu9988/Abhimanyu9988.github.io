#!/usr/bin/env bash
# The free check, run on a GitHub runner with Docker before AWS is paid for. It builds the lab's real source and runs it with the
# real agent against ElasticMQ, which stands in for SQS and keeps AWSTraceHeader. Kubernetes, Splunk and real SQS are not tested here.
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh
mkdir -p c/src c/out c/mq c/load

python3 - <<'PY'
import re
def block(path, key):
    text = open(path).read()
    m = re.search(rf"^  {re.escape(key)}: \|\n((?:    .*\n|\n)+)", text, re.M)
    return "\n".join(line[4:] for line in m[1].splitlines()) + "\n"
open("c/src/pom.xml", "w").write(block("k8s/00-build.yaml", "pom.xml"))
open("c/src/App.java", "w").write(block("k8s/00-build.yaml", "App.java"))
open("c/mq/elasticmq.conf", "w").write(block("k8s/15-standin-sqs.yaml", "elasticmq.conf"))
open("c/load/load.py", "w").write(block("k8s/20-load.yaml", "load.py"))
PY

IMAGE=$(grep -m1 -oE 'maven:[^ ]+' k8s/10-app.yaml)
PY_IMAGE=$(grep -m1 -oE 'python:[^ ]+' k8s/20-load.yaml)
MQ_IMAGE=$(grep -m1 -oE 'softwaremill/elasticmq-native:[^ ]+' k8s/15-standin-sqs.yaml)
AGENT_URL=$(grep -m1 -oE 'https://github.com/[^"]+\.jar' k8s/00-build.yaml)
QUEUE_URL=http://sqs:9324/<account-id>/lab-orders
docker network create lab >/dev/null

echo "== Fetching the agent and building the jar"
docker run --rm -v "$PWD/c/out:/lab" "$PY_IMAGE" python -c "import urllib.request; urllib.request.urlretrieve('$AGENT_URL', '/lab/opentelemetry-javaagent.jar')"
docker run --rm -v "$PWD/c/src:/src:ro" -v "$PWD/c/out:/lab" "$IMAGE" sh -c '
  set -e; mkdir -p /w/src/main/java/lab; cp /src/pom.xml /w/; cp /src/App.java /w/src/main/java/lab/; cd /w
  mvn -q -B -DskipTests package; cp target/app.jar /lab/app.jar'
ls -l c/out
echo "$(grep -m1 -oE '[0-9a-f]{64}' k8s/00-build.yaml)  c/out/opentelemetry-javaagent.jar" | sha256sum -c -

# grep reads the whole log here, because a grep that stops early breaks the pipe and pipefail would report that as no match.
logged() { docker logs "$1" 2>&1 | grep "$2" >/dev/null; }

docker run -d --name sqs --network lab -v "$PWD/c/mq/elasticmq.conf:/opt/elasticmq.conf:ro" "$MQ_IMAGE" >/dev/null
for i in $(seq 1 30); do logged sqs 'ElasticMQ server (.*started' && break; sleep 1; done
logged sqs 'ElasticMQ server (.*started' || { docker logs sqs 2>&1 | tail -30; echo "CHECK FAILED: the stand-in didn't start"; exit 1; }

start() {   # service, JAVA_TOOL_OPTIONS
  docker run -d --name "$1" --network lab -v "$PWD/c/out:/lab:ro" --entrypoint java \
    -e "SPRING_PROFILES_ACTIVE=$1" -e QUEUE_NAME=lab-orders -e AWS_REGION=eu-central-1 -e SPRING_CLOUD_AWS_SQS_LISTENER_MAX_MESSAGES_PER_POLL=10 \
    -e SPRING_CLOUD_AWS_SQS_ENDPOINT=http://sqs:9324 -e AWS_ACCESS_KEY_ID=standin -e AWS_SECRET_ACCESS_KEY=<redacted> \
    -e "JAVA_TOOL_OPTIONS=$2" "$IMAGE" -Xms256m -Xmx256m -jar /lab/app.jar >/dev/null
}
ready() {
  for i in $(seq 1 90); do logged "$1" 'process running for' && break; sleep 1; done
  docker logs "$1" 2>&1 | grep -oE 'process running for [0-9.]+' | tail -1 | sed "s/^/$1 /"
  logged "$1" 'process running for' || { docker logs "$1" 2>&1 | tail -40 | cut -c1-240; echo "CHECK FAILED: $1 didn't start"; exit 1; }
}
load() { docker run --rm --name load --network lab -v "$PWD/c/load:/load:ro" -e "MESSAGES=$1" "$PY_IMAGE" python /load/load.py; }
drained() {   # how many orders fulfilment must report
  handled=0
  for i in $(seq 1 30); do
    handled=$(docker logs fulfilment 2>&1 | fulfilled_count)
    [ "$handled" -ge "$1" ] && break
    sleep 1
  done
  echo "fulfilled: $handled of $1"
  [ "$handled" -ge "$1" ] || { docker logs fulfilment 2>&1 | tail -30 | cut -c1-240; echo "CHECK FAILED: not every order was handled"; exit 1; }
}
collect() {
  docker logs orders > c/orders.log 2>&1
  docker logs fulfilment > c/fulfilment.log 2>&1
  count_shared c/orders.log c/fulfilment.log
  count_links c/fulfilment.log
  count_receive c/fulfilment.log
  echo "$1: receive spans $receive_spans, producer spans $produced, consumer spans $consumed, joined $joined, sharing one trace $shared, distinct traces $traces, linked to the producer $linked, most process spans for one message ${most:-0}"
}
probe() {   # sets header, with fulfilment paused so the message waits on the queue
  docker stop -t 20 fulfilment >/dev/null
  docker rm fulfilment >/dev/null
  docker exec orders curl -sS --max-time 20 -X POST "http://localhost:8080/orders/$PROBE_ORDER" | tee c/probe.json
  echo
  docker exec orders curl -sS --max-time 20 -H 'Content-Type: application/x-amz-json-1.0' -H 'X-Amz-Target: AmazonSQS.ReceiveMessage' -d "$(peek_body)" http://sqs:9324/ > c/peek.json
  found=$(pick_message "$(jq -r '.messageId' c/probe.json)" < c/peek.json)
  [ -n "$found" ] || { cut -c1-300 c/peek.json; echo "CHECK FAILED: the probe message wasn't on the queue"; exit 1; }
  header=$(jq -r '.Attributes.AWSTraceHeader // empty' <<<"$found")
  echo "AWSTraceHeader on the message: ${header:-none}"
  docker exec orders curl -sS --max-time 20 -H 'Content-Type: application/x-amz-json-1.0' -H 'X-Amz-Target: AmazonSQS.DeleteMessage' \
    -d "$(jq -cn --arg q "$QUEUE_URL" --arg r "$(jq -r '.ReceiptHandle' <<<"$found")" '{QueueUrl: $q, ReceiptHandle: $r}')" http://sqs:9324/ >/dev/null
}

AGENT="-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.metrics.exporter=none -Dotel.logs.exporter=none"
ORDERS="$AGENT -Dotel.traces.exporter=console -Dotel.service.name=orders"
FULFILMENT="$AGENT -Dotel.traces.exporter=console,logging-otlp -Dotel.service.name=fulfilment"

echo "== No agent"
start fulfilment ""
start orders ""
ready fulfilment
ready orders
load 60 | tee c/before.txt
grep -q 'errors=0' c/before.txt || { docker logs orders 2>&1 | tail -30 | cut -c1-240; echo "CHECK FAILED: orders failed with no agent"; exit 1; }
drained 60
collect "no agent"
probe
[ -z "$header" ] || { echo "CHECK FAILED: a message carried a trace header with no agent"; exit 1; }
docker rm -f orders >/dev/null

echo "== With the agent on its defaults"
start fulfilment "$FULFILMENT"
start orders "$ORDERS"
ready fulfilment
ready orders
docker logs orders 2>&1 | grep 'opentelemetry-javaagent - version' || true
load 100 | tee c/after.txt
grep -q 'errors=0' c/after.txt || { docker logs orders 2>&1 | tail -30 | cut -c1-240; echo "CHECK FAILED: orders failed with the agent"; exit 1; }
drained 100
sleep 7
collect "default run"
{ grep -m1 ' PRODUCER \[' c/orders.log || true; } | sed -E 's/^.*LoggingSpanExporter - //' | cut -c1-420
{ grep -m1 ' CONSUMER \[' c/fulfilment.log || true; } | sed -E 's/^.*LoggingSpanExporter - //' | cut -c1-420
[ "$shared" -eq 100 ] || { head -5 spans-joined.txt; echo "CHECK FAILED: $shared of 100 messages shared one trace"; exit 1; }
[ "$traces" -eq 100 ] || { echo "CHECK FAILED: expected one trace per order, got $traces"; exit 1; }
[ "$most" -eq 1 ] || { echo "CHECK FAILED: a message got $most process spans"; exit 1; }
[ "$consumed" -eq 100 ] || { echo "CHECK FAILED: $consumed process spans, so a probe message came back"; exit 1; }
[ "$receive_spans" -eq 0 ] || { echo "CHECK FAILED: $receive_spans receive spans on the defaults"; exit 1; }

probe
root=$(trace_root <<<"$header")
sleep 2
docker logs orders > c/orders-probe.log 2>&1
id=$(jq -r '.messageId' c/probe.json)
span_trace=$(span_rows PRODUCER c/orders-probe.log | awk -v m="$id" '$1 == m {print $2}')
echo "trace ID in the header: ${root:-none}, on the producer span: ${span_trace:-none}"
[ -n "$root" ] && [ "$root" = "$span_trace" ] || { echo "CHECK FAILED: the header doesn't carry the producer's trace"; exit 1; }

echo "== One setting changed: receive telemetry on, on fulfilment only"
start fulfilment "$FULFILMENT -Dotel.instrumentation.common.messaging.experimental.receive-telemetry.enabled=true"
ready fulfilment
docker rm -f orders >/dev/null
start orders "$ORDERS"
ready orders
load 40 | tee c/receive.txt
drained 40
sleep 7
collect "receive telemetry run"
[ "$joined" -eq 40 ] || { echo "CHECK FAILED: joined $joined of 40"; exit 1; }
[ "$shared" -lt 40 ] || { echo "CHECK FAILED: the setting changed nothing"; exit 1; }
[ "$most" -eq 1 ] || { echo "CHECK FAILED: a message got $most process spans"; exit 1; }
echo "links on process spans $linked, on receive spans $linked_receive, process spans under a receive span $under_receive"
span_tree
[ "$receive_spans" -gt 0 ] || { echo "CHECK FAILED: no receive spans, so the agent did not apply the setting"; exit 1; }
[ "$linked" -eq 40 ] || { echo "CHECK FAILED: $linked of 40 process spans link to the producer"; exit 1; }
echo "CHECK PASSED"
