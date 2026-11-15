#!/usr/bin/env bash
# The free check, run on a GitHub runner with Docker before AWS is paid for. It builds the lab's real source and runs it with the
# real agent against ElasticMQ, which stands in for SQS: today's defaults, receive telemetry, then both again with the 3.0 preview flag.
# Kubernetes, Splunk and real SQS are not tested here. The preview's trace structure is printed, not asserted: that is the lab's result.
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
QUEUE_URL=http://sqs:9324/000000000000/lab-orders
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

start() {   # service, JAVA_TOOL_OPTIONS, preview: 0|1
  local pv=()
  [ "${3:-0}" = 1 ] && pv=(-e OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true)
  docker rm -f "$1" >/dev/null 2>&1 || true
  docker run -d --name "$1" --network lab -v "$PWD/c/out:/lab:ro" --entrypoint java \
    -e "SPRING_PROFILES_ACTIVE=$1" -e QUEUE_NAME=lab-orders -e AWS_REGION=eu-central-1 -e SPRING_CLOUD_AWS_SQS_LISTENER_MAX_MESSAGES_PER_POLL=10 \
    -e SPRING_CLOUD_AWS_SQS_ENDPOINT=http://sqs:9324 -e AWS_ACCESS_KEY_ID=standin -e AWS_SECRET_ACCESS_KEY=standin \
    "${pv[@]}" -e "JAVA_TOOL_OPTIONS=$2" "$IMAGE" -Xms256m -Xmx256m -jar /lab/app.jar >/dev/null
}
ready() {
  for i in $(seq 1 90); do logged "$1" 'process running for' && break; sleep 1; done
  docker logs "$1" 2>&1 | grep -oE 'process running for [0-9.]+' | tail -1 | sed "s/^/$1 /"
  logged "$1" 'process running for' || { docker logs "$1" 2>&1 | tail -40 | cut -c1-240; echo "CHECK FAILED: $1 didn't start"; exit 1; }
}
load() { docker run --rm --name load --network lab -v "$PWD/c/load:/load:ro" -e "MESSAGES=$1" -e "FIRST=$2" "$PY_IMAGE" python /load/load.py; }
drained() {   # how many orders fulfilment must report, first order number
  handled=0
  for i in $(seq 1 30); do
    handled=$(docker logs fulfilment 2>&1 | fulfilled_count "$2" $(( $2 + $1 - 1 )))
    [ "$handled" -ge "$1" ] && break
    sleep 1
  done
  echo "fulfilled: $handled of $1"
  [ "$handled" -ge "$1" ] || { docker logs fulfilment 2>&1 | tail -30 | cut -c1-240; echo "CHECK FAILED: not every order was handled"; exit 1; }
}
QUEUE_NAME=lab-orders
run() {   # label, messages, preview 0|1, receive 0|1
  local f="$FULFILMENT"
  [ "$4" = 1 ] && f="$f -Dotel.instrumentation.common.messaging.experimental.receive-telemetry.enabled=true"
  echo "== $1"
  start fulfilment "$f" "$3"
  start orders "$ORDERS" "$3"
  ready fulfilment
  ready orders
  docker logs orders 2>&1 | grep -m1 'opentelemetry-javaagent - version' || true
  docker inspect orders --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^OTEL_' || echo "no OTEL_ variables"
  load "$2" "$(first_order "$1")" | tee "c/$1.txt"
  grep -q 'errors=0' "c/$1.txt" || { docker logs orders 2>&1 | tail -30 | cut -c1-240; echo "CHECK FAILED: orders failed in $1"; exit 1; }
  drained "$2" "$(first_order "$1")"
  sleep 7
  docker logs orders > c/orders.log 2>&1
  docker logs fulfilment > c/fulfilment.log 2>&1
  count_shared c/orders.log c/fulfilment.log
  count_links c/fulfilment.log
  count_receive c/fulfilment.log
  count_styles c/orders.log c/fulfilment.log
  echo "$1: producer spans $produced, process spans $consumed, joined $joined, sharing one trace $shared, in a different trace $different, linked to the producer $linked, receive or poll spans $receive_spans, process spans under a receive span $under_receive, most process spans for one message ${most:-0}"
  echo "$1: named operation first $new_style, named queue first $old_style"
  span_names c/orders.log c/fulfilment.log
  span_tree
  [ "$produced" -eq "$2" ] || { echo "CHECK FAILED: $produced producer spans for $2 orders"; exit 1; }
  [ "$no_producer" -eq 0 ] || { echo "CHECK FAILED: $no_producer process spans with no producer in this run, so a message came from another run"; exit 1; }
  [ "$joined" -eq "$2" ] || { head -5 spans-producer.txt spans-consumer.txt; echo "CHECK FAILED: joined $joined of $2 on message ID, so the process span wasn't recognised"; exit 1; }
  [ "${most:-0}" -eq 1 ] || { echo "CHECK FAILED: a message got $most process spans"; exit 1; }
  if [ "$4" = 1 ]; then
    [ "$receive_spans" -gt 0 ] || { echo "CHECK FAILED: no receive or poll spans in $1, so the agent did not apply the setting"; exit 1; }
  else
    [ "$receive_spans" -eq 0 ] || { echo "CHECK FAILED: $receive_spans receive spans with receive telemetry off"; exit 1; }
  fi
  docker rm -f orders fulfilment >/dev/null
}

AGENT="-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.metrics.exporter=none -Dotel.logs.exporter=none"
ORDERS="$AGENT -Dotel.traces.exporter=console -Dotel.service.name=orders"
FULFILMENT="$AGENT -Dotel.traces.exporter=console,logging-otlp -Dotel.service.name=fulfilment"

run default 100 0 0
[ "$shared" -eq 100 ] || { echo "CHECK FAILED: today's defaults kept $shared of 100 messages in one trace, not the 100 the SQS lab measured"; exit 1; }
run receive_telemetry 40 0 1
run preview 100 1 0
[ "$new_style" -gt 0 ] || [ "$old_style" -gt 0 ] || { echo "CHECK FAILED: no producer or process span names found in the preview run"; exit 1; }
run receive_preview 40 1 1
echo "CHECK PASSED"
