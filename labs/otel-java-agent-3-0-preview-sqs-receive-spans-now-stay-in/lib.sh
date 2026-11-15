# Shared by reproduce.sh and fix.sh: the load, the queue drain, attaching the agent and the report. Sourced, never run on its own.
export LC_ALL=C
. "$(dirname "${BASH_SOURCE[0]}")/base.sh"     # the 14-minute clock, fail and emit, and where the queue is
. "$(dirname "${BASH_SOURCE[0]}")/spans.sh"    # reads the spans the agent printed to a pod log


# Distinct orders a log says were handled, counting only this run's order numbers, so a message left from another run can't count.
fulfilled_count() {   # first order number, last order number. Reads stdin.
  { grep -oE 'fulfilled "?order-[0-9]+' || true; } | sed -E 's/.*order-//' | awk -v a="$1" -v b="$2" '$1 >= a && $1 <= b' | sort -u | wc -l
}

# Order numbers per run: 1-400, 1001-1100, 2001-2400, 3001-3100.
first_order() { case "$1" in default) echo 1 ;; receive_telemetry) echo 1001 ;; preview) echo 2001 ;; receive_preview) echo 3001 ;; esac; }

# ---------- the rest is for reproduce.sh and fix.sh, on Kubernetes ----------

queue_counts() {   # "<visible> <in flight>" on the queue
  local body
  body=$(jq -cn --arg q "$QUEUE_URL" '{QueueUrl: $q, AttributeNames: ["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"]}')
  if standin; then
    kubectl -n demo exec deploy/orders -c app -- curl -sS --max-time 20 -H 'Content-Type: application/x-amz-json-1.0' -H 'X-Amz-Target: AmazonSQS.GetQueueAttributes' -d "$body" http://sqs:9324/
  else
    aws sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --output json
  fi | jq -r '"\(.Attributes.ApproximateNumberOfMessages // "unknown") \(.Attributes.ApproximateNumberOfMessagesNotVisible // "unknown")"'
}

# The queue must be empty before a run changes anything, so no old message reaches the new pods.
drained_queue() {   # seconds allowed
  local i q
  for i in $(seq 1 $(( $1 / 5 + 1 ))); do
    q=$(queue_counts)
    [ "$q" = "0 0" ] && break
    sleep 5
  done
  echo "queue before the change: visible ${q% *}, in flight ${q#* }"
  [ "$q" = "0 0" ] || fail "the queue still holds messages: $q"
}

load_run() {   # run label, message count, file for the output, seconds to wait. Sets first, sent and handled.
  first=$(first_order "$1")
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  sed -e "s/LOAD_NODE/$LOAD_NODE/" -e "s/value: \"400\"/value: \"$2\"/" -e "s/value: \"FIRST\"/value: \"$first\"/" k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$4s" || { kubectl -n demo logs load --tail=20; why orders; fail "the load did not finish"; }
  kubectl -n demo logs load | tee "$3"
  sent=$(grep -oE 'sent=[0-9]+' "$3" | cut -d= -f2)
  [ "${sent:-0}" -gt 0 ] || { why orders; fail "no order reached the queue"; }
  local i
  handled=0
  for i in $(seq 1 30); do
    handled=$(kubectl -n demo logs "$(newest fulfilment)" -c app 2>/dev/null | fulfilled_count "$first" $(( first + $2 - 1 )))
    [ "$handled" -ge "$sent" ] && break
    [ "$(remaining)" -lt 200 ] && break
    sleep 5
  done
}

# A run counts if at least 98% of its orders were handled. The exact numbers are reported either way, out of joined.
enough() { [ $(( handled * 100 )) -ge $(( sent * 98 )) ] || fail "only $handled of $sent orders were fulfilled"; }

collect() {   # run label. Saves both services' logs, then counts the join, the links, receive spans and names.
  kubectl -n demo logs "$(newest orders)" -c app > "orders-$1.log" 2>/dev/null
  kubectl -n demo logs "$(newest fulfilment)" -c app > "fulfilment-$1.log" 2>/dev/null
  count_shared "orders-$1.log" "fulfilment-$1.log"
  count_links "fulfilment-$1.log"
  count_receive "fulfilment-$1.log"
  count_styles "orders-$1.log" "fulfilment-$1.log"
}

AGENT_VERSION=2.33.1    # pinned in k8s/00-build.yaml with its SHA-256. The behaviour this lab measures is this version's.
RECEIVE=-Dotel.instrumentation.common.messaging.experimental.receive-telemetry.enabled=true

# console and logging-otlp also print spans to the pod log, so the lab can count every message and read links.
options() {   # service, environment label, exporters, then any extra setting
  echo "-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$1 -Dotel.exporter.otlp.endpoint=http://\$(HOST_IP):4318 -Dotel.resource.attributes=deployment.environment=$2,deployment.environment.name=$2 -Dotel.logs.exporter=none -Dotel.traces.exporter=$3${4:+ $4}"
}

# Puts both services on one configuration and proves it from inside each pod. The preview is one variable on both services; the only
# other difference between modes is the environment label, which only groups traces in Splunk.
attach() {   # run label, seconds allowed
  local env=$LAB_NAME flag="OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW-" extra="" s p version want=absent got
  case "$1" in preview|receive_preview) env="$LAB_NAME-v3"; flag="OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true"; want=true ;; esac
  case "$1" in receive_*) extra=$RECEIVE ;; esac
  drained_queue 60
  # LAB_RUN changes every run, so both services always get fresh pods in one rollout and each pod log holds one run only.
  kubectl -n demo set env deployment/orders "JAVA_TOOL_OPTIONS=$(options orders "$env" otlp,console)" "$flag" "LAB_RUN=$1" >/dev/null || fail "could not set orders"
  kubectl -n demo set env deployment/fulfilment "JAVA_TOOL_OPTIONS=$(options fulfilment "$env" otlp,console,logging-otlp "$extra")" "$flag" "LAB_RUN=$1" >/dev/null || fail "could not set fulfilment"
  kubectl -n demo rollout status deploy/fulfilment --timeout="$(cap "$2" 300)s" >/dev/null || { why fulfilment; fail "fulfilment did not roll out"; }
  kubectl -n demo rollout status deploy/orders --timeout="$(cap "$2" 300)s" >/dev/null || { why orders; fail "orders did not roll out"; }
  : > "config-$1.txt"
  for s in orders fulfilment; do
    p=$(newest "$s")
    version=$(kubectl -n demo logs "$p" -c app 2>/dev/null | grep -m1 -oE 'opentelemetry-javaagent - version: [0-9.]+')
    { echo "$s $p"; echo "  $version"; echo "  image: $(kubectl -n demo get "$p" -o jsonpath='{.spec.containers[0].image}')"
      echo "  agent jar: $(kubectl -n demo exec "$p" -c app -- sha256sum /lab/opentelemetry-javaagent.jar | cut -c1-64)"
      kubectl -n demo exec "$p" -c app -- env | grep -E '^(OTEL_|JAVA_TOOL_OPTIONS)' | sed 's/^/  /'; } | tee -a "config-$1.txt"
    [ "${version##* }" = "$AGENT_VERSION" ] || fail "$s runs agent ${version:-unknown}, not $AGENT_VERSION"
    got=$(kubectl -n demo exec "$p" -c app -- env | sed -n 's/^OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=//p')
    echo "$s preview flag inside the pod: ${got:-absent}"
    [ "${got:-absent}" = "$want" ] || fail "$s preview flag is ${got:-absent}, the $1 run needs $want"
  done
  [ "$(kubectl -n demo exec "$(newest orders)" -c app -- sha256sum /lab/app.jar | awk '{print $1}')" = "$(cat jar-sha.txt)" ] && echo "jar unchanged from the first run" || fail "the jar changed, so the comparison would be unfair"
}

# One run's counts, raw first and then as HERO_MEASURE lines. Every count is out of joined, the messages whose producer and process
# spans were both found. no producer or no process span means a message the join couldn't pair.
report() {   # run label
  echo "$1 run: orders $first to $(( first + sent - 1 )), sent $sent, fulfilled $handled, producer spans $produced, process spans $consumed, joined on message ID $joined"
  echo "$1 run: of $joined joined, sharing one trace $shared, in a different trace $different, process spans linked to their producer trace $linked"
  echo "$1 run: unpaired, no process span $no_consumer, no producer span $no_producer. Most process spans for one message: ${most:-0}"
  echo "$1 run: receive or poll spans $receive_spans, linked to a producer trace $linked_receive, process spans whose parent is one $under_receive"
  echo "$1 run: producer and process spans named operation first $new_style, queue first $old_style"
  echo "-- $1 run, messaging span names and kinds the agent printed"
  span_names "orders-$1.log" "fulfilment-$1.log"
  echo "-- $1 run, three joined rows: message ID, producer trace, process trace"
  awk 'NR <= 3 {print "  " $0}' spans-joined.txt
  echo "HERO_MEASURE: messages_fulfilled[$1]=$handled"
  echo "HERO_MEASURE: joined_messages[$1]=$joined"
  echo "HERO_MEASURE: one_trace_messages[$1]=$shared"
  echo "HERO_MEASURE: process_links_to_producer[$1]=$linked"
  echo "HERO_MEASURE: unpaired_no_producer[$1]=$no_producer"
  echo "HERO_MEASURE: unpaired_no_process[$1]=$no_consumer"
  case "$1" in default|preview) echo "HERO_MEASURE: new_style_span_names[$1]=$new_style"; echo "HERO_MEASURE: legacy_style_span_names[$1]=$old_style" ;;
               *) echo "HERO_MEASURE: receive_spans[$1]=$receive_spans"; echo "HERO_MEASURE: receive_links_to_producer[$1]=$linked_receive"
                  echo "HERO_MEASURE: process_under_receive[$1]=$under_receive" ;; esac
}
