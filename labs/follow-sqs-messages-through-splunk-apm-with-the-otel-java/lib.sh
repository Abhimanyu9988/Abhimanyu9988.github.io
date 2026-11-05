# Shared by the lab's scripts. Sourced, never run on its own.
export LC_ALL=C
PROBE_ORDER=999999    # the one message read from the queue by hand, kept out of every count

# The clock and the end of a script: every wait is capped so the script always prints its result inside the 14 minutes.
start_clock() {   # seconds allowed, the result to print on failure
  START=$(date +%s)
  LIMIT=$1
  FAILED=$2
  HERO_DONE=0
  trap '[ "$HERO_DONE" = 1 ] || { HERO_DONE=1; echo "HERO_RESULT: $FAILED"; }' EXIT
}
remaining() { echo $(( START + LIMIT - $(date +%s) )); }
cap() { local want=$1 reserve=$2 r; r=$(( $(remaining) - reserve )); [ "$r" -lt 5 ] && r=5; [ "$want" -lt "$r" ] && echo "$want" || echo "$r"; }
emit() { HERO_DONE=1; echo "HERO_RESULT: $1"; }
fail() { echo "FAIL: $*"; [ "$HERO_DONE" = 1 ] || emit "$FAILED"; exit 0; }

standin() { [[ "${LAB_NAME:-}" == rehearsal-* ]]; }    # true only in the free rehearsal, where ElasticMQ plays SQS

queue_settings() {
  if standin; then
    QUEUE_NAME=lab-orders
    QUEUE_URL=http://sqs:9324/<account-id>/lab-orders
  else
    QUEUE_NAME=$(terraform output -raw queue_name)
    QUEUE_URL=$(terraform output -raw queue_url)
  fi
}

# "<message ID> <trace ID>" for spans the agent printed. The producer span is the publish span of kind PRODUCER.
# The consumer span is the process span: kind CONSUMER, named '<queue> process'. A receive span is never counted.
span_rows() {   # kind, log files
  local kind=$1 name=""
  shift
  [ "$kind" = CONSUMER ] && name=" process"
  { grep -h LoggingSpanExporter "$@" || true; } |
    sed -nE "s/.*'[^']*$name' : ([0-9a-f]{32}) [0-9a-f]{16} $kind \[.*messaging\.message\.id=([0-9a-fA-F-]+).*/\2 \1/p" | sort -u
}

# Joins producer and consumer spans on the SQS message ID. Sets produced, consumed, joined, shared, traces and the three ways a message can miss.
count_shared() {   # log files
  span_rows PRODUCER "$@" > spans-producer.txt
  span_rows CONSUMER "$@" > spans-consumer.txt
  join spans-producer.txt spans-consumer.txt > spans-joined.txt
  produced=$(cut -d' ' -f1 spans-producer.txt | sort -u | wc -l)
  consumed=$(cut -d' ' -f1 spans-consumer.txt | sort -u | wc -l)
  joined=$(cut -d' ' -f1 spans-joined.txt | sort -u | wc -l)
  shared=$(awk '$2 == $3 {print $1}' spans-joined.txt | sort -u | wc -l)
  traces=$(awk '$2 == $3 {print $2}' spans-joined.txt | sort -u | wc -l)
  most=$(cut -d' ' -f1 spans-consumer.txt | sort | uniq -c | sort -rn | awk 'NR == 1 {print $1}')
  no_consumer=$(( produced - joined ))
  no_producer=$(( consumed - joined ))
  different=$(( joined - shared ))
}

# Links read from the OTLP JSON the agent prints, because the console format leaves them out. A link counts only when its trace ID is that
# message's producer trace. Process spans and receive spans are counted apart, so the lab shows which span holds the link.
count_links() {   # log files. Needs spans-producer.txt. Sets linked, linked_receive and under_receive.
  { grep -h '"scopeSpans"' "$@" || true; } | sed -E 's/^[^{]*//' | jq -Rc 'fromjson? | .scopeSpans[]?.spans[]?' > spans-otlp.jsonl
  jq -r 'select(.name | endswith(" process")) | ([.attributes[]? | select(.key == "messaging.message.id") | .value.stringValue][0]) as $m | .links[]? | "\($m) \(.traceId)"' spans-otlp.jsonl | sort -u > spans-links.txt
  linked=$(join spans-links.txt spans-producer.txt | awk '$2 == $3 {print $1}' | sort -u | wc -l)
  jq -r 'select(.name | endswith(" receive")) | .links[]?.traceId' spans-otlp.jsonl | sort -u > spans-receive-links.txt
  linked_receive=$(cut -d' ' -f2 spans-producer.txt | sort -u | comm -12 - spans-receive-links.txt | wc -l)
  under_receive=$(jq -rs '(map(select(.name | endswith(" receive")) | .spanId)) as $r | map(select((.name | endswith(" process")) and ((.parentSpanId // "") as $p | $r | index($p) != null))) | length' spans-otlp.jsonl)
}

# One whole consumer trace from the OTLP JSON, each span indented under its parent, so a reader can see which span holds the link.
span_tree() {
  jq -rs '
    def line($d): ("  " * ($d + 1)) + "\(.name) kind=\(.kind) span=\(.spanId) parent=\(.parentSpanId // "none") links=\([.links[]?.traceId] | join(","))";
    (map(select((.name | endswith(" process")) and ((.links // []) | length > 0))) + map(select(.name | endswith(" receive"))) | first) as $s
    | if $s == null then "  no consumer span with a link" else
        map(select(.traceId == $s.traceId)) as $t | ($t | map(.spanId)) as $ids
        | def branch($s; $d): ($s | line($d)), ($t[] | select(.parentSpanId == $s.spanId) | branch(.; $d + 1));
          $t[] | select((.parentSpanId // "") as $p | $ids | index($p) == null) | branch(.; 0)
      end' spans-otlp.jsonl
}

# Spans named '<queue> receive' of kind CONSUMER. The positive control: the agent makes these only when receive telemetry is really on.
count_receive() {   # log files. Sets receive_spans.
  receive_spans=$({ grep -h LoggingSpanExporter "$@" || true; } | { grep -E " receive' : [0-9a-f]{32} [0-9a-f]{16} CONSUMER " || true; } | wc -l)
}

# Distinct orders a log says were handled. Reads stdin, and the probe message doesn't count.
fulfilled_count() {
  { grep -oE 'fulfilled "?order-[0-9]+' || true; } | { grep -v "order-$PROBE_ORDER\$" || true; } | sort -u | wc -l
}

# The request body that reads waiting messages and their system attribute. A visibility of 0 leaves them on the queue.
peek_body() {
  jq -cn --arg q "$QUEUE_URL" '{QueueUrl: $q, MessageSystemAttributeNames: ["AWSTraceHeader"], MaxNumberOfMessages: 10, VisibilityTimeout: 0, WaitTimeSeconds: 5}'
}

pick_message() { jq -c --arg id "$1" '[.Messages[]? | select(.MessageId == $id)] | first // empty' 2>/dev/null; }    # reads stdin

trace_root() {   # "Root=1-69fa1b2c-0123456789abcdef01234567;Parent=..." becomes the 32 hex digit OTel trace ID
  sed -nE 's/.*Root=1-([0-9a-f]{8})-([0-9a-f]{24}).*/\1\2/p'
}

# ---------- the rest is for reproduce.sh and fix.sh, on Kubernetes ----------
newest() { kubectl -n demo get pods -l "app=$1" --field-selector=status.phase=Running --sort-by=.metadata.creationTimestamp -o name | tail -1; }
why() { kubectl -n demo get pods -o wide | tail -8 | cut -c1-120; kubectl -n demo get events --sort-by=.lastTimestamp | tail -10 | cut -c1-180; kubectl -n demo logs deploy/"$1" -c app --tail=25 2>/dev/null | cut -c1-220; }

load_run() {   # message count, file for the output, seconds to wait. Sets sent and handled.
  kubectl -n demo delete pod load --ignore-not-found --wait=true >/dev/null 2>&1
  sed -e "s/LOAD_NODE/$LOAD_NODE/" -e "s/value: \"400\"/value: \"$1\"/" k8s/20-load.yaml | kubectl apply -f - >/dev/null || fail "could not start the load"
  kubectl -n demo wait pod/load --for=jsonpath='{.status.phase}'=Succeeded --timeout="$3s" || { kubectl -n demo logs load --tail=20; why orders; fail "the load did not finish"; }
  kubectl -n demo logs load | tee "$2"
  sent=$(grep -oE 'sent=[0-9]+' "$2" | cut -d= -f2)
  [ "${sent:-0}" -gt 0 ] || { why orders; fail "no order reached the queue"; }
  local i
  handled=0
  for i in $(seq 1 18); do
    handled=$(kubectl -n demo logs "$(newest fulfilment)" -c app 2>/dev/null | fulfilled_count)
    [ "$handled" -ge "$sent" ] && break
    sleep 5
  done
}

collect() {   # label. Saves both services' logs, then counts the join and the links.
  kubectl -n demo logs "$(newest orders)" -c app > "orders-$1.log" 2>/dev/null
  kubectl -n demo logs "$(newest fulfilment)" -c app > "fulfilment-$1.log" 2>/dev/null
  count_shared "orders-$1.log" "fulfilment-$1.log"
  count_links "fulfilment-$1.log"
  count_receive "fulfilment-$1.log"
}

queue_depth() {   # visible messages, printed for the record
  if standin; then
    kubectl -n demo exec deploy/orders -c app -- curl -sS --max-time 20 -H 'Content-Type: application/x-amz-json-1.0' -H 'X-Amz-Target: AmazonSQS.GetQueueAttributes' \
      -d "$(jq -cn --arg q "$QUEUE_URL" '{QueueUrl: $q, AttributeNames: ["ApproximateNumberOfMessages"]}')" http://sqs:9324/ | jq -r '.Attributes.ApproximateNumberOfMessages // "unknown"'
  else
    aws sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names ApproximateNumberOfMessages --query Attributes.ApproximateNumberOfMessages --output text
  fi
}

# Pauses fulfilment, sends one order, and finds that exact message on the queue by its ID. Sets header and probe_id.
probe() {
  local i found=""
  kubectl -n demo scale deploy/fulfilment --replicas=0 >/dev/null || fail "could not pause fulfilment"
  kubectl -n demo wait --for=delete pod -l app=fulfilment --timeout=60s >/dev/null 2>&1 || true
  echo "messages waiting before the probe: $(queue_depth)"
  kubectl -n demo exec deploy/orders -c app -- curl -sS --max-time 20 -X POST "http://localhost:8080/orders/$PROBE_ORDER" | tee probe.json
  echo
  probe_id=$(jq -r '.messageId // empty' probe.json 2>/dev/null)
  [ -n "$probe_id" ] || fail "the probe order was not sent"
  for i in 1 2 3 4 5 6; do
    if standin; then
      kubectl -n demo exec deploy/orders -c app -- curl -sS --max-time 20 -H 'Content-Type: application/x-amz-json-1.0' -H 'X-Amz-Target: AmazonSQS.ReceiveMessage' -d "$(peek_body)" http://sqs:9324/ > peek.json
    else
      aws sqs receive-message --queue-url "$QUEUE_URL" --message-system-attribute-names AWSTraceHeader --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 5 --output json > peek.json
    fi
    found=$(pick_message "$probe_id" < peek.json)
    [ -n "$found" ] && break
  done
  [ -n "$found" ] || fail "the probe message $probe_id was not found on the queue"
  header=$(jq -r '.Attributes.AWSTraceHeader // empty' <<<"$found")
  echo "message $probe_id AWSTraceHeader: ${header:-none}"
  # Deleted here, so a probe from one configuration is never handled during the next run.
  local receipt
  receipt=$(jq -r '.ReceiptHandle // empty' <<<"$found")
  [ -n "$receipt" ] || fail "the probe message has no receipt handle"
  if standin; then
    kubectl -n demo exec deploy/orders -c app -- curl -sS --max-time 20 -H 'Content-Type: application/x-amz-json-1.0' -H 'X-Amz-Target: AmazonSQS.DeleteMessage' \
      -d "$(jq -cn --arg q "$QUEUE_URL" --arg r "$receipt" '{QueueUrl: $q, ReceiptHandle: $r}')" http://sqs:9324/ >/dev/null || fail "the probe message was not deleted"
  else
    aws sqs delete-message --queue-url "$QUEUE_URL" --receipt-handle "$receipt" || fail "the probe message was not deleted"
  fi
  echo "probe message deleted"
}

resume() {   # seconds to wait
  kubectl -n demo scale deploy/fulfilment --replicas=1 >/dev/null || fail "could not resume fulfilment"
  kubectl -n demo rollout status deploy/fulfilment --timeout="$1s" >/dev/null || fail "fulfilment did not resume"
}

# What decided the result, read from each pod rather than assumed. A setting that isn't present is at the agent's default of false.
effective() {   # service
  local opts
  opts=$(kubectl -n demo get "$(newest "$1")" -o json | jq -r '.spec.containers[0].env[]? | select(.name == "JAVA_TOOL_OPTIONS") | .value')
  echo "$1 receive telemetry: $(grep -oE 'receive-telemetry\.enabled=[a-z]+' <<<"$opts" || echo 'not set, default false')"
  echo "$1 propagator for messaging: $(grep -oE 'use-propagator-for-messaging=[a-z]+' <<<"$opts" || echo 'not set, default false')"
  echo "$1 OTEL_ environment variables: $(kubectl -n demo get "$(newest "$1")" -o json | jq '[.spec.containers[0].env[]?.name | select(startswith("OTEL_"))] | length')"
}
