# Reads the spans the agent printed to a pod log. Sourced by lib.sh and check.sh, never run on its own. Pure text parsing: no cluster,
# no network. Today's agent names spans queue first ('<queue> publish', '<queue> process', '<queue> receive'); the 3.0 preview names them
# operation first ('send <queue>', 'process <queue>', 'receive <queue>' or 'poll <queue>'). Every function here accepts both.

# "<message ID> <trace ID>" for spans the agent printed. The producer span is the span of kind PRODUCER. The consumer span is the process
# span: kind CONSUMER, named '<queue> process' by today's agent or 'process <queue>' by the 3.0 preview. A receive or poll span is never counted.
span_rows() {   # kind, log files
  local kind=$1 name="([^']*)"
  shift
  [ "$kind" = CONSUMER ] && name="([^']* process|process [^']*)"
  { grep -h LoggingSpanExporter "$@" || true; } |
    sed -nE "s/.*'$name' : ([0-9a-f]{32}) [0-9a-f]{16} $kind \[.*messaging\.message\.id=([0-9a-fA-F-]+).*/\3 \2/p" | sort -u
}

# How the agent named its messaging spans, with the count of each name and kind. The queue name is shown as <queue>.
span_names() {   # log files
  { grep -h LoggingSpanExporter "$@" || true; } | sed -nE "s/.*'([^']*)' : [0-9a-f]{32} [0-9a-f]{16} ([A-Z]+) .*/\2 \1/p" |
    { grep -F "$QUEUE_NAME" || true; } | sed "s/$QUEUE_NAME/<queue>/g" | sort | uniq -c | awk '{printf "  %s spans: %s %s %s\n", $1, $2, $3, $4}'
}

# Producer and process spans by naming style. Sets new_style (send <queue>, process <queue>) and old_style (<queue> publish, <queue> process).
count_styles() {   # log files
  local names
  names=$({ grep -h LoggingSpanExporter "$@" || true; } | sed -nE "s/.*'([^']*)' : [0-9a-f]{32} [0-9a-f]{16} (PRODUCER|CONSUMER) .*/\1/p")
  new_style=$({ grep -xE "(send|process) $QUEUE_NAME" <<<"$names" || true; } | grep -c . || true)
  old_style=$({ grep -xE "$QUEUE_NAME (publish|process)" <<<"$names" || true; } | grep -c . || true)
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
  jq -r 'select(.name | endswith(" process") or startswith("process ")) | ([.attributes[]? | select(.key == "messaging.message.id") | .value.stringValue][0]) as $m | .links[]? | "\($m) \(.traceId)"' spans-otlp.jsonl | sort -u > spans-links.txt
  linked=$(join spans-links.txt spans-producer.txt | awk '$2 == $3 {print $1}' | sort -u | wc -l)
  jq -r 'select(.name | endswith(" receive") or startswith("receive ") or startswith("poll ")) | .links[]?.traceId' spans-otlp.jsonl | sort -u > spans-receive-links.txt
  linked_receive=$(cut -d' ' -f2 spans-producer.txt | sort -u | comm -12 - spans-receive-links.txt | wc -l)
  under_receive=$(jq -rs '(map(select(.name | endswith(" receive") or startswith("receive ") or startswith("poll ")) | .spanId)) as $r | map(select((.name | endswith(" process") or startswith("process ")) and ((.parentSpanId // "") as $p | $r | index($p) != null))) | length' spans-otlp.jsonl)
}

# One whole consumer trace from the OTLP JSON, each span indented under its parent, so a reader can see which span holds the link.
span_tree() {
  jq -rs '
    def line($d): ("  " * ($d + 1)) + "\(.name) kind=\(.kind) span=\(.spanId) parent=\(.parentSpanId // "none") links=\([.links[]?.traceId] | join(","))";
    (map(select((.name | endswith(" process") or startswith("process ")) and ((.links // []) | length > 0))) + map(select(.name | endswith(" receive") or startswith("receive ") or startswith("poll "))) | first) as $s
    | if $s == null then "  no consumer span with a link" else
        map(select(.traceId == $s.traceId)) as $t | ($t | map(.spanId)) as $ids
        | def branch($s; $d): ($s | line($d)), ($t[] | select(.parentSpanId == $s.spanId) | branch(.; $d + 1));
          $t[] | select((.parentSpanId // "") as $p | $ids | index($p) == null) | branch(.; 0)
      end' spans-otlp.jsonl
}

# Receive spans: '<queue> receive' of kind CONSUMER today, 'receive <queue>' or 'poll <queue>' of kind CONSUMER or CLIENT in the preview.
# The positive control: the agent makes these only when receive telemetry is really on.
count_receive() {   # log files. Sets receive_spans.
  receive_spans=$({ grep -h LoggingSpanExporter "$@" || true; } | { grep -E "'([^']* receive|receive [^']*|poll [^']*)' : [0-9a-f]{32} [0-9a-f]{16} (CONSUMER|CLIENT) " || true; } | wc -l)
}
